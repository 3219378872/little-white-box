# shellcheck shell=bash
# Loaded by ../stack.sh; functions share the stack namespace.
#
# Starting and stopping managed processes. Every launch is tagged with an owner
# token (MANAGED_PROCESS_TOKEN_ENV) and runs in its own session, so stop paths
# can prove a PID/PGID is still ours before signalling it.

# Runtime dirs hold pids, tokens and logs with secrets; keep them owner-only.
secure_runtime_paths() {
  install -d -m 700 "$RUN_DIR" "$LOG_DIR" "$PID_DIR" "$RUN_DIR/bin" "$ETC_DIR" || return $?
  find "$LOG_DIR" -maxdepth 1 -type f -name '*.log*' -exec chmod 600 {} + 2>/dev/null || true
  find "$PID_DIR" -maxdepth 1 -type f -name '*.pid' -exec chmod 600 {} + 2>/dev/null || true
}

clear_sensitive_assistant_logs() {
  # Stop and wait before cleanup so an old maintainer cannot re-enter rotation.
  # The Python helper also takes the rotation lock and removes interrupted temps.
  stop_svc log-maintainer || return $?
  python3 "$LOG_MAINTAINER_SCRIPT" "$LOG_DIR" --clear-assistant
}

# ---------------------------------------------------------------------------
# Process-group probes
# ---------------------------------------------------------------------------

# True while a process group contains at least one runnable or sleeping member.
# Linux zombies no longer execute or own sockets, but kill -0 still reports
# them, so inspect /proc state there and use kill -0 as the portable fallback.
process_group_running() {
  local pgid="$1" proc pid group pgrp _
  if [[ -d /proc ]]; then
    for proc in /proc/[0-9]*; do
      pid="${proc##*/}"
      group="$(process_stat_group "$pid" 2>/dev/null || true)"
      [[ -n "$group" ]] || continue
      IFS='|' read -r pgrp _ <<<"$group"
      if [[ "$pgrp" == "$pgid" ]]; then
        return 0
      fi
    done
    return 1
  fi
  kill -0 -- "-$pgid" 2>/dev/null
}

# True while the group is alive and, when a token is given, still carries it.
stoppable_process_group_running() {
  local pgid="$1" token="${2:-}"
  process_group_running "$pgid" || return 1
  [[ -z "$token" ]] || process_group_has_owner_token "$pgid" "$token"
}

# Stop probes return 0 while the target is alive and still ours, 1 once it is
# gone, and 2 when something alive no longer proves our ownership (PID reuse).
owned_group_stop_probe() {
  local pgid="$1" token="$2"
  if stoppable_process_group_running "$pgid" "$token"; then
    return 0
  fi
  if process_group_running "$pgid"; then
    return 2
  fi
  return 1
}

service_pid_stop_probe() {
  local name="$1" pid="$2" fence_mode="$3" expected_token="$4"
  if service_process_can_be_stopped "$name" "$pid" "$fence_mode" "$expected_token"; then
    return 0
  fi
  if service_process_matches "$name" "$pid"; then
    return 2
  fi
  return 1
}

# Polls PROBE (see above) up to POLLS+1 times, sleeping INTERVAL in between.
# Returns 0 once the target is gone, 1 when its owner changed, 2 on timeout.
wait_for_stop() {
  local polls="$1" interval="$2" probe="$3" i state
  shift 3
  for ((i = 0; ; i++)); do
    state=0
    "$probe" "$@" || state=$?
    case "$state" in
      1) return 0 ;;
      2) return 1 ;;
    esac
    if ((i >= polls)); then
      return 2
    fi
    sleep "$interval"
  done
}

# ---------------------------------------------------------------------------
# Stopping recorded services
# ---------------------------------------------------------------------------

# Prints the owner token a stop must keep matching (empty for legacy services
# without an owner file). token mode trusts a token captured by the caller;
# current/legacy read it from the owner file next to the pidfile.
stop_fence_token() {
  local name="$1" pgid="$2" fence_mode="$3" expected_token="$4" pidfile owner
  case "$fence_mode" in
    token)
      if [[ "$expected_token" != "$name:"* ]]; then
        echo "refusing to stop $name group=$pgid: invalid owner token" >&2
        return 1
      fi
      printf '%s\n' "$expected_token"
      ;;
    current | legacy)
      pidfile="$PID_DIR/$name.pid"
      owner="$(pid_owner_file "$pidfile")"
      [[ -e "$owner" || -L "$owner" ]] || return 0
      read_service_owner_token "$name" "$pidfile" || {
        echo "refusing to stop $name group=$pgid: invalid owner token" >&2
        return 1
      }
      ;;
    *)
      echo "refusing to stop $name group=$pgid: invalid stop fence mode" >&2
      return 1
      ;;
  esac
}

# TERM a managed process group, wait out the grace period, then KILL. Every
# step re-checks the owner token so a recycled PGID is never signalled.
stop_owned_process_group() {
  local name="$1" pgid="$2" fence_mode="${3:-current}" expected_token="${4:-}"
  local token wait_status=0
  token="$(stop_fence_token "$name" "$pgid" "$fence_mode" "$expected_token")" || return $?

  # Already gone: nothing to stop. Alive without our token: not ours to stop.
  process_group_running "$pgid" || return 0
  if ! stoppable_process_group_running "$pgid" "$token"; then
    echo "refusing to stop $name group=$pgid: owner token mismatch" >&2
    return 1
  fi

  # Graceful TERM. A failed kill only matters if the group is still ours.
  if ! kill -- "-$pgid" 2>/dev/null && stoppable_process_group_running "$pgid" "$token"; then
    echo "failed to send TERM to $name group=$pgid" >&2
    return 1
  fi
  wait_for_stop "$STOP_POLLS" "$STOP_TERM_INTERVAL" \
    owned_group_stop_probe "$pgid" "$token" || wait_status=$?
  case "$wait_status" in
    0) return 0 ;;
    1)
      echo "not escalating stop for $name group=$pgid: owner token changed" >&2
      return 1
      ;;
  esac

  # Still ours after the grace period: escalate to KILL.
  if ! kill -9 -- "-$pgid" 2>/dev/null && stoppable_process_group_running "$pgid" "$token"; then
    echo "failed to send KILL to $name group=$pgid" >&2
    return 1
  fi
  wait_status=0
  wait_for_stop "$STOP_POLLS" "$STOP_KILL_INTERVAL" \
    owned_group_stop_probe "$pgid" "$token" || wait_status=$?
  case "$wait_status" in
    0) return 0 ;;
    1) echo "$name group $pgid changed owner while stopping" >&2 ;;
    *) echo "$name process group $pgid survived KILL" >&2 ;;
  esac
  return 1
}

# Stops one identified service PID. Managed launches lead their own session,
# so a live group is handed to stop_owned_process_group; otherwise the single
# process gets the same TERM -> wait -> KILL sequence with ownership checks.
stop_tree() {
  local name="$1" pid="$2" fence_mode="${3:-current}" expected_token="${4:-}"
  local wait_status=0

  # A same-binary process without our token is a reused PID: refuse loudly.
  # A PID that no longer looks like the service has simply exited.
  if ! service_process_can_be_stopped "$name" "$pid" "$fence_mode" "$expected_token"; then
    if service_process_matches "$name" "$pid"; then
      echo "refusing to stop $name pid=$pid: owner token mismatch" >&2
      return 1
    fi
    return 0
  fi

  if process_group_running "$pid"; then
    stop_owned_process_group "$name" "$pid" "$fence_mode" "$expected_token"
    return $?
  fi

  # Graceful TERM for the lone process.
  if ! kill "$pid" 2>/dev/null &&
    service_process_can_be_stopped "$name" "$pid" "$fence_mode" "$expected_token"; then
    echo "failed to send TERM to $name pid=$pid" >&2
    return 1
  fi
  wait_for_stop "$STOP_POLLS" "$STOP_TERM_INTERVAL" \
    service_pid_stop_probe "$name" "$pid" "$fence_mode" "$expected_token" || wait_status=$?
  case "$wait_status" in
    0) return 0 ;;
    1)
      echo "not escalating stop for $name: pid $pid owner token changed" >&2
      return 1
      ;;
  esac

  # Still ours after the grace period: escalate to KILL.
  if ! kill -9 "$pid" 2>/dev/null &&
    service_process_can_be_stopped "$name" "$pid" "$fence_mode" "$expected_token"; then
    echo "failed to send KILL to $name pid=$pid" >&2
    return 1
  fi
  wait_status=0
  wait_for_stop "$STOP_POLLS" "$STOP_KILL_INTERVAL" \
    service_pid_stop_probe "$name" "$pid" "$fence_mode" "$expected_token" || wait_status=$?
  case "$wait_status" in
    0) return 0 ;;
    1) echo "$name pid $pid changed owner while stopping" >&2 ;;
    *) echo "$name process tree $pid survived KILL" >&2 ;;
  esac
  return 1
}

# Public stop entry: resolves the pidfile into a live process, an orphaned
# process group, a foreign process or stale state, and only removes the
# pidfile once the owned process is really gone.
stop_svc() {
  local name="$1"
  local pidfile="$PID_DIR/$name.pid"
  local pid="" owner ready stop_status
  owner="$(pid_owner_file "$pidfile")"
  ready="$(pid_ready_file "$pidfile")"
  if pid="$(validated_service_pid "$name" "$pidfile")"; then
    # Normal case: the recorded leader is alive and proves ownership.
    echo "stopping $name pid=$pid"
    if stop_tree "$name" "$pid"; then
      :
    else
      stop_status=$?
      echo "keeping pidfile for $name because the process did not stop" >&2
      return "$stop_status"
    fi
  elif pid="$(read_service_pidfile "$pidfile" 2>/dev/null)" &&
    managed_process_group_matches "$name" "$pid" "$pidfile"; then
    # The leader exited but children in its session still carry our token.
    echo "stopping orphaned $name group=$pid"
    if stop_owned_process_group "$name" "$pid"; then
      :
    else
      stop_status=$?
      echo "keeping pidfile for $name because the process group did not stop" >&2
      return "$stop_status"
    fi
  elif pid="$(read_service_pidfile "$pidfile" 2>/dev/null)" &&
    service_process_matches "$name" "$pid" &&
    [[ -e "$owner" || -L "$owner" ]]; then
    # Same binary, wrong token: keep the evidence for manual inspection.
    echo "refusing to stop $name pid=$pid: owner token mismatch; keeping pidfile" >&2
    return 1
  elif [[ -e "$pidfile" || -L "$pidfile" || -e "$owner" || -L "$owner" ||
    -e "$ready" || -L "$ready" ]]; then
    # Nothing alive matches the recorded state; just clear it.
    remove_stale_service_state "$name" "$pidfile" || return $?
    return 0
  else
    return 0
  fi
  remove_service_state "$pidfile"
}

# ---------------------------------------------------------------------------
# Stopping a process that was started moments ago
# ---------------------------------------------------------------------------

# A just-forked child may not have exec'd yet, so it may lack the owner token;
# it is still ours while it remains our direct child.
started_process_can_be_stopped() {
  local pid="$1" token="${2:-}" parent
  process_pid_running "$pid" || return 1
  [[ -z "$token" ]] && return 0
  process_has_owner_token "$pid" "$token" && return 0
  parent="$(process_parent_pid "$pid")" || return 1
  [[ "$parent" == "$BASHPID" ]]
}

# Classifies a just-started tree into the variable named by $3:
#   group   - its session group is alive and ours (setsid already ran)
#   pid     - only the direct child is alive and ours
#   gone    - nothing left to stop
#   foreign - alive but no longer provably ours; never signal it
# Not a $(...) helper: started_process_can_be_stopped compares against BASHPID.
started_tree_state() {
  # The local must not be called "state": printf -v would assign it instead of
  # the caller's variable of that name.
  local pid="$1" token="$2" state_var="$3" classified
  if process_group_running "$pid"; then
    if [[ -n "$token" ]] && ! process_group_has_owner_token "$pid" "$token"; then
      classified=foreign
    else
      classified=group
    fi
  elif ! process_pid_running "$pid"; then
    classified=gone
  elif started_process_can_be_stopped "$pid" "$token"; then
    classified=pid
  else
    classified=foreign
  fi
  printf -v "$state_var" '%s' "$classified"
}

# Sends TERM or KILL to a started tree's group or lone pid. A failed kill is
# only an error while the target is still ours.
signal_started_tree() {
  local name="$1" pid="$2" token="$3" scope="$4" signal="$5"
  local -a kill_args=()
  [[ "$signal" == KILL ]] && kill_args=(-9)
  if [[ "$scope" == group ]]; then
    kill ${kill_args[@]+"${kill_args[@]}"} -- "-$pid" 2>/dev/null && return 0
    stoppable_process_group_running "$pid" "$token" || return 0
    echo "failed to send $signal to newly started $name group=$pid" >&2
  else
    kill ${kill_args[@]+"${kill_args[@]}"} "$pid" 2>/dev/null && return 0
    started_process_can_be_stopped "$pid" "$token" || return 0
    echo "failed to send $signal to newly started $name pid=$pid" >&2
  fi
  return 1
}

# Fence cleanup with the launch token (or the still-direct child relation
# before exec publishes that token) so PID reuse cannot redirect signals.
stop_started_tree() {
  local name="$1" pid="$2" token="${3:-}" state signalled i
  if ! safe_pid "$pid"; then
    echo "cannot stop newly started $name: unsafe pid ${pid:-empty}" >&2
    return 1
  fi

  # Initial TERM to whatever form the tree has right now.
  started_tree_state "$pid" "$token" state
  case "$state" in
    gone) return 0 ;;
    foreign)
      echo "cannot stop newly started $name pid=$pid: owner token mismatch" >&2
      return 1
      ;;
  esac
  signal_started_tree "$name" "$pid" "$token" "$state" TERM || return $?
  signalled="$state"

  # Short grace period (a fresh process has nothing to flush). If the child
  # turns into a session leader meanwhile, TERM its whole group as well.
  for ((i = 0; i < STOP_POLLS; i++)); do
    started_tree_state "$pid" "$token" state
    case "$state" in
      gone) return 0 ;;
      foreign)
        echo "not escalating newly started $name pid=$pid: owner token changed" >&2
        return 1
        ;;
      group)
        if [[ "$signalled" != group ]]; then
          signal_started_tree "$name" "$pid" "$token" group TERM || return $?
          signalled=group
        fi
        ;;
    esac
    sleep "$STOP_KILL_INTERVAL"
  done

  # Escalate to KILL on whatever is still ours.
  started_tree_state "$pid" "$token" state
  case "$state" in
    gone) return 0 ;;
    foreign)
      echo "not escalating newly started $name pid=$pid: owner token changed" >&2
      return 1
      ;;
  esac
  signal_started_tree "$name" "$pid" "$token" "$state" KILL || return $?
  [[ "$state" != group ]] || signalled=group

  # Wait for the KILL to land, still refusing to touch a recycled PID. Once the
  # tree has been handled as a group, the group's exit is what counts.
  for ((i = 0; i < STOP_POLLS; i++)); do
    started_tree_state "$pid" "$token" state
    case "$state" in
      gone) return 0 ;;
      pid) [[ "$signalled" != group ]] || return 0 ;;
      foreign)
        echo "newly started $name tree $pid changed owner while stopping" >&2
        return 1
        ;;
    esac
    sleep "$STOP_KILL_INTERVAL"
  done
  echo "newly started $name process tree $pid survived KILL" >&2
  return 1
}

# ---------------------------------------------------------------------------
# Launching
# ---------------------------------------------------------------------------

# Owner tokens are unique per launch: name, launcher pid, randomness and time.
new_managed_process_token() {
  local name="$1"
  printf '%s:%s:%s:%s\n' \
    "$name" "$BASHPID" "$RANDOM" "$(date +%s%N)"
}

# app-up rolls back exactly the services it started, in reverse order.
track_app_started_service() {
  [[ "$APP_UP_TRACK_STARTS" == "1" ]] || return 0
  APP_UP_STARTED_SERVICES+=("$1")
}

# Clears whatever a previous run left behind before a fresh launch: stops an
# orphaned group of ours, refuses a foreign process, removes stale files.
prepare_service_start_state() {
  local name="$1" pidfile="$2" pid="" owner ready
  owner="$(pid_owner_file "$pidfile")"
  ready="$(pid_ready_file "$pidfile")"
  if pid="$(read_service_pidfile "$pidfile" 2>/dev/null)"; then
    if managed_process_group_matches "$name" "$pid" "$pidfile"; then
      echo "stopping orphaned $name process group before restart (group=$pid)" >&2
      stop_svc "$name" || return $?
      return 0
    fi
    if service_process_matches "$name" "$pid" &&
      [[ -e "$owner" || -L "$owner" ]]; then
      echo "refusing to replace $name pid=$pid: owner token mismatch" >&2
      return 1
    fi
  fi
  if [[ -e "$pidfile" || -L "$pidfile" || -e "$owner" || -L "$owner" ||
    -e "$ready" || -L "$ready" ]]; then
    remove_stale_service_state "$name" "$pidfile" || return $?
  fi
}

# Publishes owner token and pid atomically (temp file + rename, mode 0600).
# If publishing fails the new process is stopped right away, because nothing
# could find it later; should that also fail, a best-effort record is kept.
record_started_pid() {
  local name="$1" pid="$2" pidfile="$3" token="$4"
  local owner pid_tmp owner_tmp status=0 cleanup_status=0
  owner="$(pid_owner_file "$pidfile")"
  pid_tmp="$pidfile.tmp.$BASHPID.$RANDOM"
  owner_tmp="$owner.tmp.$BASHPID.$RANDOM"

  # Owner first: a pidfile without its owner fence would read as legacy.
  printf '%s\n' "$token" >"$owner_tmp" || status=$?
  if [[ "$status" -eq 0 ]]; then
    chmod 600 "$owner_tmp" || status=$?
  fi
  if [[ "$status" -eq 0 ]]; then
    printf '%s\n' "$pid" >"$pid_tmp" || status=$?
  fi
  if [[ "$status" -eq 0 ]]; then
    chmod 600 "$pid_tmp" || status=$?
  fi
  if [[ "$status" -eq 0 ]]; then
    mv -f "$owner_tmp" "$owner" || status=$?
  fi
  if [[ "$status" -eq 0 ]]; then
    mv -f "$pid_tmp" "$pidfile" || status=$?
  fi
  if [[ "$status" -eq 0 ]]; then
    return 0
  fi

  echo "failed to record $name pid=$pid; stopping the newly started process" >&2
  stop_started_tree "$name" "$pid" "$token" || cleanup_status=$?
  rm -f "$pid_tmp" "$owner_tmp" 2>/dev/null || true
  if [[ "$cleanup_status" -eq 0 ]]; then
    remove_service_state "$pidfile" 2>/dev/null || true
  else
    # Preserve a usable recovery handle when possible if immediate cleanup
    # itself failed. Never replace the original pidfile error status.
    printf '%s\n' "$token" >"$owner" 2>/dev/null || true
    chmod 600 "$owner" 2>/dev/null || true
    printf '%s\n' "$pid" >"$pidfile" 2>/dev/null || true
    chmod 600 "$pidfile" 2>/dev/null || true
    echo "failed to stop unrecorded $name pid=$pid; manual cleanup required" >&2
  fi
  return "$status"
}

# Undo a launch that did not come up; keep the pidfile if the stop failed so a
# later stop_svc can still find the process.
cleanup_failed_service_start() {
  local name="$1" pid="$2" pidfile="$3" token="${4:-}" stop_status
  if stop_started_tree "$name" "$pid" "$token"; then
    remove_service_state "$pidfile"
    return $?
  else
    stop_status=$?
  fi
  echo "keeping pidfile for $name because startup cleanup failed" >&2
  return "$stop_status"
}

# The one place that starts a managed background process:
#   launch_managed_process NAME PIDFILE LOGFILE WORKDIR -- ARGV...
# ARGV runs in WORKDIR as a new session leader (setsid) with a fresh owner
# token in its environment and without the lifecycle lock fd. Its pid/token are
# recorded before returning, and a process that dies within the first moment
# is cleaned up and reported. On success LAUNCHED_PID and LAUNCHED_TOKEN hold
# the new process for callers with extra readiness checks.
launch_managed_process() {
  local name="$1" pidfile="$2" logfile="$3" workdir="$4"
  local token pid cleanup_status=0
  shift 4
  [[ "${1:-}" == "--" ]] && shift
  LAUNCHED_PID=""
  LAUNCHED_TOKEN=""
  token="$(new_managed_process_token "$name")" || return $?

  # Fork from a subshell so the recorder is the direct parent: that is what
  # lets the pid recorder stop the child before it has exec'd.
  (
    local started_pid
    cd "$workdir" || exit $?
    (
      close_app_lifecycle_lock_fd || exit $?
      exec env "$MANAGED_PROCESS_TOKEN_ENV=$token" setsid "$@"
    ) >>"$logfile" 2>&1 </dev/null &
    started_pid=$!
    record_started_pid "$name" "$started_pid" "$pidfile" "$token" || exit $?
  ) || return $?

  # Catch processes that exit immediately (bad flags, port in use, ...).
  sleep "$LAUNCH_SETTLE_SECONDS"
  pid="$(<"$pidfile")"
  if ! validated_service_pid "$name" "$pidfile" >/dev/null; then
    cleanup_failed_service_start "$name" "$pid" "$pidfile" "$token" || cleanup_status=$?
    echo "$name exited during startup; see $logfile" >&2
    [[ "$cleanup_status" -eq 0 ]] || return "$cleanup_status"
    return 1
  fi
  LAUNCHED_PID="$pid"
  LAUNCHED_TOKEN="$token"
}

# Builds and starts one Go service from its row in RPC_SERVICES/MQ_SERVICES
# (or the gateway). The binary is copied to RUN_DIR/bin so its path doubles as
# the process identity checked by service_identity.
start_svc() {
  local name="$1" workdir="$2" bin="$3"
  shift 3
  local pidfile="$PID_DIR/$name.pid"
  local logfile="$LOG_DIR/$name.log"
  local executable="$RUN_DIR/bin/$name"
  local pid token ready_status cleanup_status=0
  secure_runtime_paths || return $?
  touch "$logfile" || return $?
  chmod 600 "$logfile" || return $?

  # Idempotent: a verified running instance is kept. The assistant agent must
  # additionally have published post-canary readiness for this exact launch.
  if pid="$(validated_service_pid "$name" "$pidfile")"; then
    if [[ "$name" == "assistant-agent" ]]; then
      normalize_assistant_agent_metrics_port || return $?
      token="$(read_service_owner_token "$name" "$pidfile")" || return $?
      if ! assistant_agent_ready_matches "$pid" "$token"; then
        echo "assistant-agent pid=$pid is running without verified post-canary readiness; restart it" >&2
        return 1
      fi
    fi
    echo "already running: $name pid=$pid"
    return 0
  fi
  prepare_service_start_state "$name" "$pidfile" || return $?

  # Build into a temp path and rename, so a failed build never replaces the
  # binary a still-running process might be identified by.
  echo "building $name"
  (
    cd "$workdir" || exit $?
    go build -o "$executable.tmp" "$bin"
  ) >>"$logfile" 2>&1 || return $?
  mv -f "$executable.tmp" "$executable" || return $?

  # The agent's readiness marker is a log line; drop old lines first so the
  # previous run's marker cannot satisfy this launch.
  if [[ "$name" == "assistant-agent" ]]; then
    python3 "$LOG_MAINTAINER_SCRIPT" "$LOG_DIR" --clear-name assistant-agent || return $?
  fi

  echo "starting $name"
  launch_managed_process "$name" "$pidfile" "$logfile" "$workdir" -- \
    "$executable" "$@" || return $?

  # The agent is only usable after its canary run; wait for that explicitly.
  if [[ "$name" == "assistant-agent" ]]; then
    pid="$LAUNCHED_PID"
    token="$LAUNCHED_TOKEN"
    if wait_assistant_agent_ready "$pid" "$token" "$logfile"; then
      :
    else
      ready_status=$?
      cleanup_failed_service_start "$name" "$pid" "$pidfile" "$token" || cleanup_status=$?
      echo "$name failed post-canary readiness; see $logfile" >&2
      [[ "$cleanup_status" -eq 0 ]] || return "$cleanup_status"
      return "$ready_status"
    fi
  fi
  track_app_started_service "$name"
}

# Background log rotation for every app log; idempotent like start_svc.
start_log_maintainer() {
  local pidfile="$PID_DIR/log-maintainer.pid"
  secure_runtime_paths || return $?
  if validated_service_pid log-maintainer "$pidfile" >/dev/null; then
    return 0
  fi
  prepare_service_start_state log-maintainer "$pidfile" || return $?
  # The maintainer rotates LOG_DIR itself, so it must not log into it.
  launch_managed_process log-maintainer "$pidfile" /dev/null "$ROOT" -- \
    python3 "$LOG_MAINTAINER_SCRIPT" "$LOG_DIR" \
    --max-bytes "$LOG_MAX_BYTES" --interval "$LOG_ROTATE_INTERVAL_SECONDS" || return $?
  track_app_started_service log-maintainer
}

# Starts one "name|workdir|bin|flag|conf" service row from config.sh.
start_row() {
  local row="$1" name workdir bin flag conf
  IFS='|' read -r name workdir bin flag conf <<<"$row"
  start_svc "$name" "$workdir" "$bin" "$flag" "$conf"
}

# ---------------------------------------------------------------------------
# Port ownership
# ---------------------------------------------------------------------------

# PIDs listening on PORT on a loopback or wildcard address (other interfaces
# are ignored on purpose: only those can collide with the local stack).
listening_port_pids() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    (ss -H -ltnp "sport = :$port" 2>/dev/null || true) | awk -v port="$port" '
      {
        local_address = $4
        if (local_address != "127.0.0.1:" port &&
            local_address != "0.0.0.0:" port &&
            local_address != "*:" port &&
            local_address != "[::]:" port &&
            local_address != "[::ffff:127.0.0.1]:" port) {
          next
        }
        line = $0
        while (match(line, /pid=[0-9]+/)) {
          print substr(line, RSTART + 4, RLENGTH - 4)
          line = substr(line, RSTART + RLENGTH)
        }
      }' | sort -u
    return 0
  fi
  # lsof's @127.0.0.1 filter misses wildcard listeners (*:port), so list every
  # listener on the port and apply the same address allowlist as ss above.
  if command -v lsof >/dev/null 2>&1; then
    (lsof -nP -iTCP:"$port" -sTCP:LISTEN -F pn 2>/dev/null || true) | awk -v port="$port" '
      /^p[0-9]+$/ { pid = substr($0, 2); next }
      /^n/ {
        local_address = substr($0, 2)
        if (pid != "" &&
            (local_address == "127.0.0.1:" port ||
             local_address == "0.0.0.0:" port ||
             local_address == "*:" port ||
             local_address == "[::]:" port ||
             local_address == "[::ffff:127.0.0.1]:" port)) {
          print pid
        }
      }' | sort -u
  fi
}

# Fallback for app-down: stop listeners on a service port that are provably
# ours even if the pidfile is gone, and report anything else holding the port.
stop_owned_port() {
  local name="$1" port="$2" fence_mode="${3:-current}" expected_token="${4:-}"
  local pid found=0 step_status status=0
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    found=1
    if service_process_can_be_stopped "$name" "$pid" "$fence_mode" "$expected_token"; then
      echo "stopping orphaned $name on :$port pid=$pid"
      if stop_tree "$name" "$pid" "$fence_mode" "$expected_token"; then
        :
      else
        step_status=$?
        [[ "$status" -ne 0 ]] || status="$step_status"
      fi
    else
      echo "leaving unknown process on :$port pid=$pid (not managed $name)" >&2
      [[ "$status" -ne 0 ]] || status=1
    fi
  done < <(listening_port_pids "$port")

  # The port must be free afterwards, otherwise the next app-up would collide.
  if port_open 127.0.0.1 "$port"; then
    if [[ "$found" == "0" ]]; then
      echo "leaving unknown process on :$port (owner could not be verified)" >&2
    else
      echo "critical port :$port remains occupied after stopping $name" >&2
    fi
    [[ "$status" -ne 0 ]] || status=1
  fi
  return "$status"
}
