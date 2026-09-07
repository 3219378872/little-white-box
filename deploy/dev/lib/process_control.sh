# shellcheck shell=bash
# Loaded by ../stack.sh; functions share the stack namespace.

secure_runtime_paths() {
  install -d -m 700 "$RUN_DIR" "$LOG_DIR" "$PID_DIR" "$RUN_DIR/bin" "$ETC_DIR" || return $?
  find "$LOG_DIR" -maxdepth 1 -type f -name '*.log*' -exec chmod 600 {} + 2>/dev/null || true
  find "$PID_DIR" -maxdepth 1 -type f -name '*.pid' -exec chmod 600 {} + 2>/dev/null || true
}

clear_sensitive_assistant_logs() {
  local name logfile
  for name in assistant-rpc assistant-watch assistant-agent; do
    logfile="$LOG_DIR/$name.log"
    if [[ -e "$logfile" && ! -L "$logfile" ]]; then
      : >"$logfile" || return $?
      chmod 600 "$logfile" || return $?
    fi
    rm -f "$logfile.1.gz" || return $?
  done
}

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

stoppable_process_group_running() {
  local pgid="$1" token="${2:-}"
  process_group_running "$pgid" || return 1
  [[ -z "$token" ]] || process_group_has_owner_token "$pgid" "$token"
}

stop_owned_process_group() {
  local name="$1" pgid="$2" fence_mode="${3:-current}" expected_token="${4:-}"
  local i pidfile owner token=""
  if [[ "$fence_mode" == "token" ]]; then
    if [[ "$expected_token" != "$name:"* ]]; then
      echo "refusing to stop $name group=$pgid: invalid owner token" >&2
      return 1
    fi
    token="$expected_token"
  elif [[ "$fence_mode" == "current" || "$fence_mode" == "legacy" ]]; then
    pidfile="$PID_DIR/$name.pid"
    owner="$(pid_owner_file "$pidfile")"
    if [[ -e "$owner" || -L "$owner" ]]; then
      token="$(read_service_owner_token "$name" "$pidfile")" || {
        echo "refusing to stop $name group=$pgid: invalid owner token" >&2
        return 1
      }
    fi
  else
    echo "refusing to stop $name group=$pgid: invalid stop fence mode" >&2
    return 1
  fi
  if ! process_group_running "$pgid"; then
    return 0
  fi
  if ! stoppable_process_group_running "$pgid" "$token"; then
    echo "refusing to stop $name group=$pgid: owner token mismatch" >&2
    return 1
  fi
  if ! kill -- "-$pgid" 2>/dev/null && stoppable_process_group_running "$pgid" "$token"; then
    echo "failed to send TERM to $name group=$pgid" >&2
    return 1
  fi
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if ! stoppable_process_group_running "$pgid" "$token"; then
      if process_group_running "$pgid"; then
        echo "not escalating stop for $name group=$pgid: owner token changed" >&2
        return 1
      fi
      return 0
    fi
    sleep 0.2
  done
  if ! stoppable_process_group_running "$pgid" "$token"; then
    if process_group_running "$pgid"; then
      echo "not escalating stop for $name group=$pgid: owner token changed" >&2
      return 1
    fi
    return 0
  fi
  if ! kill -9 -- "-$pgid" 2>/dev/null && stoppable_process_group_running "$pgid" "$token"; then
    echo "failed to send KILL to $name group=$pgid" >&2
    return 1
  fi
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if ! stoppable_process_group_running "$pgid" "$token"; then
      if process_group_running "$pgid"; then
        echo "$name group $pgid changed owner while stopping" >&2
        return 1
      fi
      return 0
    fi
    sleep 0.1
  done
  echo "$name process group $pgid survived KILL" >&2
  return 1
}

started_process_can_be_stopped() {
  local pid="$1" token="${2:-}" parent
  process_pid_running "$pid" || return 1
  [[ -z "$token" ]] && return 0
  process_has_owner_token "$pid" "$token" && return 0
  parent="$(process_parent_pid "$pid")" || return 1
  [[ "$parent" == "$BASHPID" ]]
}

# Fence cleanup with the launch token (or the still-direct child relation
# before exec publishes that token) so PID reuse cannot redirect signals.
stop_started_tree() {
  local name="$1" pid="$2" token="${3:-}" i group_owned=0
  if ! safe_pid "$pid"; then
    echo "cannot stop newly started $name: unsafe pid ${pid:-empty}" >&2
    return 1
  fi
  if process_group_running "$pid"; then
    if [[ -n "$token" ]] && ! process_group_has_owner_token "$pid" "$token"; then
      echo "cannot stop newly started $name group=$pid: owner token mismatch" >&2
      return 1
    fi
    group_owned=1
  elif ! process_pid_running "$pid"; then
    return 0
  elif ! started_process_can_be_stopped "$pid" "$token"; then
    echo "cannot stop newly started $name pid=$pid: owner token mismatch" >&2
    return 1
  fi
  if [[ "$group_owned" == "1" ]]; then
    if ! kill -- "-$pid" 2>/dev/null && stoppable_process_group_running "$pid" "$token"; then
      echo "failed to send TERM to newly started $name group=$pid" >&2
      return 1
    fi
  else
    if ! kill "$pid" 2>/dev/null && started_process_can_be_stopped "$pid" "$token"; then
      echo "failed to send TERM to newly started $name pid=$pid" >&2
      return 1
    fi
  fi
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if process_group_running "$pid"; then
      if [[ -n "$token" ]] && ! process_group_has_owner_token "$pid" "$token"; then
        echo "not escalating newly started $name group=$pid: owner token changed" >&2
        return 1
      fi
      if [[ "$group_owned" == "0" ]]; then
        group_owned=1
        if ! kill -- "-$pid" 2>/dev/null && stoppable_process_group_running "$pid" "$token"; then
          echo "failed to send TERM to newly started $name group=$pid" >&2
          return 1
        fi
      fi
    elif ! process_pid_running "$pid"; then
      return 0
    elif ! started_process_can_be_stopped "$pid" "$token"; then
      echo "not escalating newly started $name pid=$pid: owner token changed" >&2
      return 1
    fi
    sleep 0.1
  done
  if process_group_running "$pid"; then
    if [[ -n "$token" ]] && ! process_group_has_owner_token "$pid" "$token"; then
      echo "not escalating newly started $name group=$pid: owner token changed" >&2
      return 1
    fi
    group_owned=1
    if ! kill -9 -- "-$pid" 2>/dev/null && stoppable_process_group_running "$pid" "$token"; then
      echo "failed to send KILL to newly started $name group=$pid" >&2
      return 1
    fi
  elif process_pid_running "$pid"; then
    if ! started_process_can_be_stopped "$pid" "$token"; then
      echo "not escalating newly started $name pid=$pid: owner token changed" >&2
      return 1
    fi
    if ! kill -9 "$pid" 2>/dev/null && started_process_can_be_stopped "$pid" "$token"; then
      echo "failed to send KILL to newly started $name pid=$pid" >&2
      return 1
    fi
  else
    return 0
  fi
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if [[ "$group_owned" == "1" ]]; then
      if ! stoppable_process_group_running "$pid" "$token"; then
        if process_group_running "$pid"; then
          echo "newly started $name group $pid changed owner while stopping" >&2
          return 1
        fi
        return 0
      fi
    elif ! process_pid_running "$pid"; then
      return 0
    elif ! started_process_can_be_stopped "$pid" "$token"; then
      echo "newly started $name pid $pid changed owner while stopping" >&2
      return 1
    fi
    sleep 0.1
  done
  echo "newly started $name process tree $pid survived KILL" >&2
  return 1
}

new_managed_process_token() {
  local name="$1"
  printf '%s:%s:%s:%s\n' \
    "$name" "$BASHPID" "$RANDOM" "$(date +%s%N)"
}

track_app_started_service() {
  [[ "$APP_UP_TRACK_STARTS" == "1" ]] || return 0
  APP_UP_STARTED_SERVICES+=("$1")
}

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

record_started_pid() {
  local name="$1" pid="$2" pidfile="$3" token="$4"
  local owner pid_tmp owner_tmp status=0 cleanup_status=0
  owner="$(pid_owner_file "$pidfile")"
  pid_tmp="$pidfile.tmp.$BASHPID.$RANDOM"
  owner_tmp="$owner.tmp.$BASHPID.$RANDOM"

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

start_svc() {
  local name="$1" workdir="$2" bin="$3"
  shift 3
  local pidfile="$PID_DIR/$name.pid"
  local logfile="$LOG_DIR/$name.log"
  local bindir="$RUN_DIR/bin"
  local executable="$bindir/$name"
  local pid build_status token ready_status cleanup_status=0
  secure_runtime_paths || return $?
  touch "$logfile" || return $?
  chmod 600 "$logfile" || return $?
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
  echo "building $name"
  if (
    cd "$workdir" || exit $?
    go build -o "$executable.tmp" "$bin"
  ) >>"$logfile" 2>&1; then
    build_status=0
  else
    build_status=$?
  fi
  [[ "$build_status" -eq 0 ]] || return "$build_status"
  mv -f "$executable.tmp" "$executable" || return $?
  if [[ "$name" == "assistant-agent" ]]; then
    : >"$logfile" || return $?
    chmod 600 "$logfile" || return $?
    rm -f "$logfile.1.gz" || return $?
  fi
  echo "starting $name"
  token="$(new_managed_process_token "$name")" || return $?
  (
    local started_pid
    cd "$workdir" || exit $?
    (
      close_app_lifecycle_lock_fd || exit $?
      exec env "$MANAGED_PROCESS_TOKEN_ENV=$token" setsid "$executable" "$@"
    ) >>"$logfile" 2>&1 </dev/null &
    started_pid=$!
    record_started_pid "$name" "$started_pid" "$pidfile" "$token" || exit $?
  ) || return $?
  sleep 0.1
  pid="$(<"$pidfile")"
  if ! validated_service_pid "$name" "$pidfile" >/dev/null; then
    if cleanup_failed_service_start "$name" "$pid" "$pidfile" "$token"; then
      :
    else
      cleanup_status=$?
    fi
    echo "$name exited during startup; see $logfile" >&2
    [[ "$cleanup_status" -eq 0 ]] || return "$cleanup_status"
    return 1
  fi
  if [[ "$name" == "assistant-agent" ]]; then
    if wait_assistant_agent_ready "$pid" "$token" "$logfile"; then
      :
    else
      ready_status=$?
      if cleanup_failed_service_start "$name" "$pid" "$pidfile" "$token"; then
        :
      else
        cleanup_status=$?
      fi
      echo "$name failed post-canary readiness; see $logfile" >&2
      [[ "$cleanup_status" -eq 0 ]] || return "$cleanup_status"
      return "$ready_status"
    fi
  fi
  track_app_started_service "$name"
}

start_log_maintainer() {
  local pidfile="$PID_DIR/log-maintainer.pid"
  local pid started_pid token cleanup_status=0
  secure_runtime_paths || return $?
  if pid="$(validated_service_pid log-maintainer "$pidfile")"; then
    return 0
  fi
  prepare_service_start_state log-maintainer "$pidfile" || return $?
  token="$(new_managed_process_token log-maintainer)" || return $?
  (
    close_app_lifecycle_lock_fd || exit $?
    exec env "$MANAGED_PROCESS_TOKEN_ENV=$token" \
      setsid python3 "$ROOT/deploy/dev/log_maintainer.py" "$LOG_DIR" \
      --max-bytes "$LOG_MAX_BYTES" --interval "$LOG_ROTATE_INTERVAL_SECONDS"
  ) >/dev/null 2>&1 </dev/null &
  started_pid=$!
  record_started_pid log-maintainer "$started_pid" "$pidfile" "$token" || return $?
  sleep 0.1
  pid="$(<"$pidfile")"
  if ! validated_service_pid log-maintainer "$pidfile" >/dev/null; then
    if cleanup_failed_service_start log-maintainer "$pid" "$pidfile" "$token"; then
      :
    else
      cleanup_status=$?
    fi
    echo "log maintainer exited during startup" >&2
    [[ "$cleanup_status" -eq 0 ]] || return "$cleanup_status"
    return 1
  fi
  track_app_started_service log-maintainer
}

start_row() {
  local row="$1"
  IFS='|' read -r name workdir bin flag conf <<<"$row"
  start_svc "$name" "$workdir" "$bin" "$flag" "$conf"
}

stop_tree() {
  local name="$1" pid="$2" fence_mode="${3:-current}" expected_token="${4:-}"
  local i group_owned=0
  if ! service_process_can_be_stopped "$name" "$pid" "$fence_mode" "$expected_token"; then
    if service_process_matches "$name" "$pid"; then
      echo "refusing to stop $name pid=$pid: owner token mismatch" >&2
      return 1
    fi
    return 0
  fi
  if process_group_running "$pid"; then
    group_owned=1
  fi
  if [[ "$group_owned" == "1" ]]; then
    stop_owned_process_group "$name" "$pid" "$fence_mode" "$expected_token"
    return $?
  else
    if ! kill "$pid" 2>/dev/null &&
      service_process_can_be_stopped "$name" "$pid" "$fence_mode" "$expected_token"; then
      echo "failed to send TERM to $name pid=$pid" >&2
      return 1
    fi
  fi
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if ! service_process_can_be_stopped "$name" "$pid" "$fence_mode" "$expected_token"; then
      if service_process_matches "$name" "$pid"; then
        echo "not escalating stop for $name: pid $pid owner token changed" >&2
        return 1
      fi
      echo "not escalating stop for $name: pid $pid no longer matches" >&2
      return 0
    fi
    sleep 0.2
  done
  if ! service_process_can_be_stopped "$name" "$pid" "$fence_mode" "$expected_token"; then
    if service_process_matches "$name" "$pid"; then
      echo "not escalating stop for $name: pid $pid owner token changed" >&2
      return 1
    fi
    return 0
  fi
  if ! kill -9 "$pid" 2>/dev/null &&
    service_process_can_be_stopped "$name" "$pid" "$fence_mode" "$expected_token"; then
    echo "failed to send KILL to $name pid=$pid" >&2
    return 1
  fi
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if ! service_process_can_be_stopped "$name" "$pid" "$fence_mode" "$expected_token"; then
      if service_process_matches "$name" "$pid"; then
        echo "$name pid $pid changed owner while stopping" >&2
        return 1
      fi
      return 0
    fi
    sleep 0.1
  done
  echo "$name process tree $pid survived KILL" >&2
  return 1
}

stop_svc() {
  local name="$1"
  local pidfile="$PID_DIR/$name.pid"
  local pid="" owner ready stop_status
  owner="$(pid_owner_file "$pidfile")"
  ready="$(pid_ready_file "$pidfile")"
  if pid="$(validated_service_pid "$name" "$pidfile")"; then
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
    echo "refusing to stop $name pid=$pid: owner token mismatch; keeping pidfile" >&2
    return 1
  elif [[ -e "$pidfile" || -L "$pidfile" || -e "$owner" || -L "$owner" ||
    -e "$ready" || -L "$ready" ]]; then
    remove_stale_service_state "$name" "$pidfile" || return $?
    return 0
  else
    return 0
  fi
  remove_service_state "$pidfile"
}

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
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -t -iTCP@127.0.0.1:"$port" -sTCP:LISTEN 2>/dev/null | sort -u || true
  fi
}

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
