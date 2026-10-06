# shellcheck shell=bash
# Loaded by ../stack.sh; functions share the stack namespace.
#
# Process identity and ownership. A PID is "ours" only when it both looks like
# the service (binary path or script argument) and, once an owner file exists,
# carries the launch token in its environment. Every stop and status path goes
# through these checks so a recycled PID is never signalled or reported.

# True for the gateway and every RPC/MQ row: services run from RUN_DIR/bin.
is_managed_binary_service() {
  local wanted="$1" row name
  [[ "$wanted" == "gateway" ]] && return 0
  for row in "${RPC_SERVICES[@]}" "${MQ_SERVICES[@]}"; do
    IFS='|' read -r name _ <<<"$row"
    [[ "$name" == "$wanted" ]] && return 0
  done
  return 1
}

# A managed Go process executes its private runtime binary. Python helpers are
# identified by an exact script argument because /proc/<pid>/exe is Python.
service_identity() {
  local name="$1"
  case "$name" in
    frontend)
      printf 'arg|%s\n' "$FRONT_SERVER_SCRIPT"
      ;;
    log-maintainer)
      printf 'arg|%s\n' "$LOG_MAINTAINER_SCRIPT"
      ;;
    llm-fixture)
      printf 'arg|%s\n' "$LLM_FIXTURE_SCRIPT"
      ;;
    *)
      is_managed_binary_service "$name" || return 1
      printf 'exe|%s\n' "$RUN_DIR/bin/$name"
      ;;
  esac
}

# Rejects non-numeric PIDs, init/0, and this shell itself as signal targets.
safe_pid() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  ((10#$pid > 1)) || return 1
  [[ "$pid" != "$$" && "$pid" != "$BASHPID" ]]
}

# Absolute, symlink-resolved path (falls back to the parent dir when the file
# itself is gone, e.g. a replaced binary).
canonical_path() {
  local path="$1" resolved
  if resolved="$(readlink -f "$path" 2>/dev/null)" && [[ -n "$resolved" ]]; then
    printf '%s\n' "$resolved"
    return 0
  fi
  (
    cd "$(dirname "$path")" 2>/dev/null || exit $?
    printf '%s/%s\n' "$(pwd -P)" "$(basename "$path")"
  )
}

# True when PID runs exactly the EXPECTED binary (a replaced binary shows
# up as "path (deleted)" in /proc and still counts).
process_executable_matches() {
  local pid="$1" expected="$2" actual="" command=""
  expected="$(canonical_path "$expected")" || return 1
  if [[ -L "/proc/$pid/exe" ]]; then
    actual="$(readlink "/proc/$pid/exe" 2>/dev/null || true)"
    actual="${actual% (deleted)}"
    [[ "$actual" == "$expected" ]]
    return
  fi

  # Non-Linux fallback. Managed paths must not contain whitespace here because
  # portable ps exposes a flattened command line rather than argv boundaries.
  command="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  actual="${command%%[[:space:]]*}"
  [[ -n "$actual" ]] || return 1
  actual="$(canonical_path "$actual")" || return 1
  [[ "$actual" == "$expected" ]]
}

# True when one argv element of PID equals EXPECTED exactly.
process_has_exact_arg() {
  local pid="$1" expected="$2" arg command
  if [[ -r "/proc/$pid/cmdline" ]]; then
    while IFS= read -r -d '' arg; do
      [[ "$arg" == "$expected" ]] && return 0
    done <"/proc/$pid/cmdline"
    return 1
  fi

  # Best-effort fallback for systems without procfs; workspace paths are
  # required to be whitespace-free for an unambiguous flattened ps match.
  command="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  [[ " $command " == *" $expected "* ]]
}

# True when PID's executable is a python interpreter (python, python3, ...).
process_executable_is_python() {
  local pid="$1" actual="" command=""
  if [[ -L "/proc/$pid/exe" ]]; then
    actual="$(readlink "/proc/$pid/exe" 2>/dev/null || true)"
    actual="${actual% (deleted)}"
  else
    command="$(ps -p "$pid" -o args= 2>/dev/null || true)"
    actual="${command%%[[:space:]]*}"
  fi
  actual="${actual##*/}"
  [[ "$actual" == python || "$actual" == python[0-9]* ]]
}

# True when PID is alive and has NAME's identity (binary path or script arg).
# Identity alone is not ownership: see service_owner_matches.
service_process_matches() {
  local name="$1" pid="$2" identity kind expected
  safe_pid "$pid" || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  identity="$(service_identity "$name")" || return 1
  IFS='|' read -r kind expected <<<"$identity"
  case "$kind" in
    exe) process_executable_matches "$pid" "$expected" ;;
    arg)
      process_executable_is_python "$pid" &&
        process_has_exact_arg "$pid" "$expected"
      ;;
    *) return 1 ;;
  esac
}

# All live PIDs carrying NAME's identity, pidfile or not.
service_process_pids() {
  local name="$1" proc pid
  if [[ -d /proc ]]; then
    for proc in /proc/[0-9]*; do
      pid="${proc##*/}"
      service_process_matches "$name" "$pid" && printf '%s\n' "$pid"
    done
    return 0
  fi
  while IFS= read -r pid; do
    pid="${pid//[[:space:]]/}"
    service_process_matches "$name" "$pid" && printf '%s\n' "$pid"
  done < <(ps -e -o pid= 2>/dev/null || true)
}

# Sidecar files of a pidfile: .owner holds the launch token, .ready the
# assistant agent's verified readiness record.
pid_owner_file() {
  printf '%s.owner\n' "$1"
}

pid_ready_file() {
  printf '%s.ready\n' "$1"
}

# Prints the PID from a regular (non-symlink) pidfile if it is a safe PID.
read_service_pidfile() {
  local pidfile="$1" pid=""
  [[ -f "$pidfile" && ! -L "$pidfile" ]] || return 1
  IFS= read -r pid <"$pidfile" || return 1
  safe_pid "$pid" || return 1
  printf '%s\n' "$pid"
}

# Prints NAME's owner token from the .owner file; tokens always start "NAME:".
read_service_owner_token() {
  local name="$1" pidfile="$2" owner token=""
  owner="$(pid_owner_file "$pidfile")"
  [[ -f "$owner" && ! -L "$owner" ]] || return 1
  IFS= read -r token <"$owner" || return 1
  [[ "$token" == "$name:"* ]] || return 1
  printf '%s\n' "$token"
}

# True when PID's environment carries exactly this launch token.
process_has_owner_token() {
  local pid="$1" expected="$2" entry
  [[ -r "/proc/$pid/environ" ]] || return 1
  while IFS= read -r -d '' entry; do
    [[ "$entry" == "$MANAGED_PROCESS_TOKEN_ENV=$expected" ]] && return 0
  done <"/proc/$pid/environ"
  return 1
}

# Owner metadata is optional for processes created before token tracking was
# introduced. Once an owner file exists, however, it is a mandatory fence
# against treating a reused PID with the same executable as our process.
service_owner_matches() {
  local name="$1" pid="$2" pidfile="$3" owner token
  owner="$(pid_owner_file "$pidfile")"
  if [[ ! -e "$owner" && ! -L "$owner" ]]; then
    return 0
  fi
  token="$(read_service_owner_token "$name" "$pidfile")" || return 1
  process_has_owner_token "$pid" "$token"
}

# True when PID is NAME and still owned under FENCE_MODE:
#   token          - must carry EXPECTED_TOKEN (captured before state cleanup)
#   current/legacy - must match the owner file when one exists
service_process_can_be_stopped() {
  local name="$1" pid="$2" fence_mode="${3:-current}" expected_token="${4:-}"
  local pidfile owner
  service_process_matches "$name" "$pid" || return 1
  if [[ "$fence_mode" == "token" ]]; then
    [[ "$expected_token" == "$name:"* ]] || return 1
    process_has_owner_token "$pid" "$expected_token"
    return $?
  fi
  [[ "$fence_mode" == "current" || "$fence_mode" == "legacy" ]] || return 1
  pidfile="$PID_DIR/$name.pid"
  owner="$(pid_owner_file "$pidfile")"
  if [[ -e "$owner" || -L "$owner" ]]; then
    service_owner_matches "$name" "$pid" "$pidfile"
    return $?
  fi
  return 0
}

# app-down removes stale pid/owner files before its port fallback runs. Capture
# whether the service was token-fenced at entry so that cleanup cannot silently
# downgrade it to legacy executable-only ownership.
capture_service_stop_fence() {
  local name="$1" mode_var="$2" token_var="$3" pidfile owner token=""
  local mode="legacy"
  pidfile="$PID_DIR/$name.pid"
  owner="$(pid_owner_file "$pidfile")"
  if [[ -e "$owner" || -L "$owner" ]]; then
    mode="token"
    token="$(read_service_owner_token "$name" "$pidfile" 2>/dev/null || true)"
    if [[ -z "$token" ]]; then
      echo "preserving invalid $name owner metadata as a fail-closed stop fence" >&2
    fi
  fi
  printf -v "$mode_var" '%s' "$mode"
  printf -v "$token_var" '%s' "$token"
}

# Rewrites the captured owner token after a failed port cleanup, so the next
# app-down still treats the survivor as token-fenced.
restore_service_stop_fence() {
  local name="$1" fence_mode="$2" token="$3" pidfile owner owner_tmp status
  [[ "$fence_mode" == "token" ]] || return 0
  pidfile="$PID_DIR/$name.pid"
  owner="$(pid_owner_file "$pidfile")"
  owner_tmp="$owner.tmp.$BASHPID.$RANDOM"
  if printf '%s\n' "$token" >"$owner_tmp"; then
    :
  else
    status=$?
    rm -f "$owner_tmp" 2>/dev/null || true
    return "$status"
  fi
  if chmod 600 "$owner_tmp"; then
    :
  else
    status=$?
    rm -f "$owner_tmp" 2>/dev/null || true
    return "$status"
  fi
  if mv -f "$owner_tmp" "$owner"; then
    :
  else
    status=$?
    rm -f "$owner_tmp" 2>/dev/null || true
    return "$status"
  fi
}

# Prints "pgrp|session" for a live (non-zombie) PID from /proc/PID/stat.
# The comm field may contain spaces or ')', so parse after the last ") ".
process_stat_group() {
  local pid="$1" line fields state pgrp session
  [[ -r "/proc/$pid/stat" ]] || return 1
  line="$(<"/proc/$pid/stat")" || return 1
  fields="${line##*) }"
  read -r state _ pgrp session _ <<<"$fields" || return 1
  [[ -n "$pgrp" && -n "$session" ]] || return 1
  [[ "$state" != "Z" && "$state" != "X" ]] || return 1
  printf '%s|%s\n' "$pgrp" "$session"
}

# Prints PID's parent PID (procfs, or ps as a portable fallback).
process_parent_pid() {
  local pid="$1" line fields state parent
  if [[ -r "/proc/$pid/stat" ]]; then
    line="$(<"/proc/$pid/stat")" || return 1
    fields="${line##*) }"
    read -r state parent _ <<<"$fields" || return 1
  else
    parent="$(ps -p "$pid" -o ppid= 2>/dev/null)" || return 1
    parent="${parent//[[:space:]]/}"
  fi
  [[ "$parent" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$parent"
}

# True while PID exists and is not a zombie.
process_pid_running() {
  local pid="$1"
  safe_pid "$pid" || return 1
  if [[ -d /proc ]]; then
    process_stat_group "$pid" >/dev/null
    return $?
  fi
  kill -0 "$pid" 2>/dev/null
}

# True when some member of session-leader group PGID carries TOKEN. Our
# launches use setsid, so a managed group's pgid equals its session id.
process_group_has_owner_token() {
  local pgid="$1" token="$2" proc pid group pgrp session
  [[ -d /proc ]] || return 1
  safe_pid "$pgid" || return 1
  for proc in /proc/[0-9]*; do
    pid="${proc##*/}"
    group="$(process_stat_group "$pid" 2>/dev/null || true)"
    [[ -n "$group" ]] || continue
    IFS='|' read -r pgrp session <<<"$group"
    if [[ "$pgrp" == "$pgid" && "$session" == "$pgid" ]] &&
      process_has_owner_token "$pid" "$token"; then
      return 0
    fi
  done
  return 1
}

# Ownership metadata is inherited through exec/fork, so a process group can be
# recovered safely even after its original leader has exited.
managed_process_group_matches() {
  local name="$1" pgid="$2" pidfile="$3" token
  token="$(read_service_owner_token "$name" "$pidfile")" || return 1
  process_group_has_owner_token "$pgid" "$token"
}

# Deletes pidfile, owner and ready files (in that order).
remove_service_state() {
  local pidfile="$1" owner ready
  owner="$(pid_owner_file "$pidfile")"
  ready="$(pid_ready_file "$pidfile")"
  rm -f "$pidfile" || return $?
  rm -f "$owner" || return $?
  rm -f "$ready"
}

# Clears state whose recorded process is gone or not ours, saying so.
remove_stale_service_state() {
  local name="$1" pidfile="$2" pid="invalid"
  pid="$(read_service_pidfile "$pidfile" 2>/dev/null || true)"
  echo "removing stale pidfile for $name (pid=${pid:-invalid}; identity mismatch)" >&2
  remove_service_state "$pidfile"
}

# Prints a live, identity-checked PID. This function is intentionally read-only:
# status and migration guards must never race a concurrent start by deleting
# process ownership state.
validated_service_pid() {
  local name="$1" pidfile="$2" pid=""
  pid="$(read_service_pidfile "$pidfile")" || return 1
  if service_process_matches "$name" "$pid" &&
    service_owner_matches "$name" "$pid" "$pidfile"; then
    printf '%s\n' "$pid"
    return 0
  fi
  return 1
}

