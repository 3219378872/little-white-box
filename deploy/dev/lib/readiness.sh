# shellcheck shell=bash
# Loaded by ../stack.sh; functions share the stack namespace.

port_open() {
  local host="$1" port="$2"
  bash -c "echo >/dev/tcp/${host}/${port}" >/dev/null 2>&1
}

wait_port() {
  local host="$1" port="$2" seconds="${3:-180}" label="${4:-$host:$port}"
  local i
  for ((i = 0; i < seconds; i++)); do
    if port_open "$host" "$port"; then
      echo "ready: $label"
      return 0
    fi
    sleep 1
  done
  echo "timeout waiting for $label" >&2
  return 1
}

wait_http() {
  local url="$1" seconds="${2:-90}" label="${3:-$url}"
  local i code
  for ((i = 0; i < seconds; i++)); do
    code="$(http_code "$url")"
    if [[ "$code" == "200" ]]; then
      echo "ready: $label"
      return 0
    fi
    sleep 1
  done
  echo "timeout waiting for $label" >&2
  return 1
}

# Single capture: curl failures (timeout, refused) must yield exactly "000",
# not "000\n000" from a fallback echo racing with -w output.
http_code() {
  local code
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 "$1" 2>/dev/null || true)"
  [[ -n "$code" ]] || code="000"
  printf '%s' "$code"
}

assistant_agent_launch_matches() {
  local pid="$1" token="$2" pidfile="$PID_DIR/assistant-agent.pid"
  local current_pid current_token
  current_pid="$(read_service_pidfile "$pidfile" 2>/dev/null)" || return 1
  [[ "$current_pid" == "$pid" ]] || return 1
  current_token="$(read_service_owner_token assistant-agent "$pidfile" 2>/dev/null)" || return 1
  [[ "$current_token" == "$token" ]] || return 1
  service_process_matches assistant-agent "$pid" || return 1
  process_has_owner_token "$pid" "$token"
}

process_listens_on_port() {
  if listening_port_owner_state "$1" "$2"; then
    return 0
  fi
  return 1
}

# Returns 0 when expected_pid is among the listeners, 1 while no listener PID
# is observable yet, and 2 when the port is explicitly owned by another PID.
listening_port_owner_state() {
  local expected_pid="$1" port="$2" pid found=0
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    found=1
    [[ "$pid" == "$expected_pid" ]] && return 0
  done < <(listening_port_pids "$port")
  [[ "$found" -eq 0 ]] && return 1
  return 2
}

record_assistant_agent_ready() {
  local pid="$1" token="$2" pidfile="$PID_DIR/assistant-agent.pid"
  local ready ready_tmp status=0
  ready="$(pid_ready_file "$pidfile")"
  ready_tmp="$ready.tmp.$BASHPID.$RANDOM"
  printf '%s\n%s\n' "$pid" "$token" >"$ready_tmp" || status=$?
  if [[ "$status" -eq 0 ]]; then
    chmod 600 "$ready_tmp" || status=$?
  fi
  if [[ "$status" -eq 0 ]]; then
    mv -f "$ready_tmp" "$ready" || status=$?
  fi
  if [[ "$status" -ne 0 ]]; then
    rm -f "$ready_tmp" 2>/dev/null || true
  fi
  return "$status"
}

assistant_agent_ready_matches() {
  local pid="$1" token="$2" pidfile="$PID_DIR/assistant-agent.pid"
  local ready recorded_pid="" recorded_token=""
  ready="$(pid_ready_file "$pidfile")"
  [[ -f "$ready" && ! -L "$ready" ]] || return 1
  {
    IFS= read -r recorded_pid
    IFS= read -r recorded_token
  } <"$ready" || return 1
  [[ "$recorded_pid" == "$pid" && "$recorded_token" == "$token" ]] || return 1
  assistant_agent_launch_matches "$pid" "$token" || return 1
  process_listens_on_port "$pid" "$ASSISTANT_AGENT_METRICS_PORT" || return 1
  assistant_agent_launch_matches "$pid" "$token"
}

# go-zero launches the Prometheus server goroutine before the worker constructs
# its ServiceContext, but the listener may bind later. Bind readiness to this
# launch's immutable pid/token and the post-canary marker, then verify that the
# same process owns the metrics listener.
wait_assistant_agent_ready() {
  local pid="$1" token="$2" logfile="$3"
  local timeout="${4:-${ASSISTANT_AGENT_READY_TIMEOUT_SECONDS:-180}}"
  local label="${5:-assistant-agent}"
  local i iterations listener_status publish_status
  normalize_assistant_agent_metrics_port || return $?
  if [[ ! "$timeout" =~ ^[1-9][0-9]*$ ]]; then
    echo "invalid assistant-agent readiness timeout: $timeout" >&2
    return 1
  fi
  iterations=$((timeout * 10))
  for ((i = 0; i < iterations; i++)); do
    if ! assistant_agent_launch_matches "$pid" "$token"; then
      echo "$label exited or changed identity before readiness" >&2
      return 1
    fi
    if grep -Fqx -- "$ASSISTANT_AGENT_READY_LINE" "$logfile" 2>/dev/null; then
      if listening_port_owner_state "$pid" "$ASSISTANT_AGENT_METRICS_PORT"; then
        if ! assistant_agent_launch_matches "$pid" "$token"; then
          echo "$label changed identity while publishing readiness" >&2
          return 1
        fi
        if record_assistant_agent_ready "$pid" "$token"; then
          :
        else
          publish_status=$?
          echo "failed to record $label readiness" >&2
          return "$publish_status"
        fi
        if assistant_agent_ready_matches "$pid" "$token"; then
          echo "ready: $label"
          return 0
        fi
        echo "$label lost verified readiness while publishing readiness" >&2
        return 1
      else
        listener_status=$?
      fi
      if [[ "$listener_status" -eq 2 ]]; then
        echo "$label metrics listener on :$ASSISTANT_AGENT_METRICS_PORT is not owned by pid $pid" >&2
        return 1
      fi
    fi
    sleep 0.1 || return $?
  done
  echo "timeout waiting for $label post-canary readiness" >&2
  return 1
}

