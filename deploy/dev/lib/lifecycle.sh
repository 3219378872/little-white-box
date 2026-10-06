# shellcheck shell=bash
# Loaded by ../stack.sh; functions share the stack namespace.
#
# Public lifecycle entry points (app/stack up, down, restart, status). Each
# public NAME takes the lifecycle lock and runs NAME_locked, so composite
# commands can call the _locked variants without re-locking.

# Runs CALLBACK ARGS... under an flock on APP_LIFECYCLE_LOCK (shared for
# read-only status, exclusive for anything that changes state). The fd is
# remembered so launched processes can close it and never hold the lock.
with_app_lifecycle_lock() {
  local mode="$1" callback="$2" lock_fd status unlock_status=0
  local previous_lock_fd="${APP_LIFECYCLE_LOCK_FD:-}"
  shift 2
  secure_runtime_paths || return $?
  command -v flock >/dev/null 2>&1 || {
    echo "flock is required for application lifecycle operations" >&2
    return 1
  }
  exec {lock_fd}>"$APP_LIFECYCLE_LOCK" || return $?
  chmod 600 "$APP_LIFECYCLE_LOCK" || {
    status=$?
    exec {lock_fd}>&-
    return "$status"
  }
  if [[ "$mode" == "shared" ]]; then
    flock -s "$lock_fd" || {
      status=$?
      exec {lock_fd}>&-
      return "$status"
    }
  else
    flock -x "$lock_fd" || {
      status=$?
      exec {lock_fd}>&-
      return "$status"
    }
  fi

  APP_LIFECYCLE_LOCK_FD="$lock_fd"
  if "$callback" "$@"; then
    status=0
  else
    status=$?
  fi
  APP_LIFECYCLE_LOCK_FD="$previous_lock_fd"
  flock -u "$lock_fd" || unlock_status=$?
  exec {lock_fd}>&-
  if [[ "$status" -ne 0 ]]; then
    return "$status"
  fi
  return "$unlock_status"
}

# Called in a child right before exec: a long-lived service holding the lock
# fd would block every later lifecycle command.
close_app_lifecycle_lock_fd() {
  [[ -n "${APP_LIFECYCLE_LOCK_FD:-}" ]] || return 0
  [[ "$APP_LIFECYCLE_LOCK_FD" =~ ^[0-9]+$ ]] || {
    echo "invalid application lifecycle lock fd: $APP_LIFECYCLE_LOCK_FD" >&2
    return 1
  }
  exec {APP_LIFECYCLE_LOCK_FD}>&-
  APP_LIFECYCLE_LOCK_FD=""
}

# Every managed app process name, in start order.
all_app_names() {
  local row name
  for row in "${RPC_SERVICES[@]}" "${MQ_SERVICES[@]}"; do
    IFS='|' read -r name _ <<<"$row"
    printf '%s\n' "$name"
  done
  printf '%s\n' gateway frontend log-maintainer
}

# Final app-up check: every managed process is still alive and ours.
validate_all_app_processes() {
  local name pidfile
  while IFS= read -r name; do
    pidfile="$PID_DIR/$name.pid"
    if ! validated_service_pid "$name" "$pidfile" >/dev/null; then
      echo "$name is not running after application startup" >&2
      return 1
    fi
  done < <(all_app_names)
}

# The ordered app-up sequence. search-rpc is held back until search-mq has
# created the index (search-rpc panics at startup without it).
app_up_steps() {
  # Environment, runtime dirs, old sensitive logs and rendered configs.
  load_env || return $?
  ensure_assistant_db_env || return $?
  secure_runtime_paths || return $?
  clear_sensitive_assistant_logs || return $?
  wipe_legacy_assistant_redis || return $?
  prepare_etc || return $?
  start_log_maintainer || return $?

  # RPC tier (minus search-rpc), then wait for the first RPC and MQ topics.
  local row name search_rpc_row=""
  for row in "${RPC_SERVICES[@]}"; do
    IFS='|' read -r name _ <<<"$row"
    if [[ "$name" == search-rpc ]]; then
      search_rpc_row="$row"
      continue
    fi
    start_row "$row" || return $?
  done
  wait_port 127.0.0.1 "$USER_RPC_PORT" 240 user-rpc || return $?
  wait_topics 180 || return $?

  # Consumers; slot search-rpc in right after search-mq made its index.
  for row in "${MQ_SERVICES[@]}"; do
    start_row "$row" || return $?
    IFS='|' read -r name _ <<<"$row"
    if [[ "$name" == search-mq && -n "$search_rpc_row" ]]; then
      wait_search_index 120 || return $?
      start_row "$search_rpc_row" || return $?
      search_rpc_row=""
    fi
  done
  if [[ -n "$search_rpc_row" ]]; then
    echo "search-rpc was not started: search-mq is missing from MQ_SERVICES" >&2
    return 1
  fi

  # Edge: gateway, frontend and the same-origin proxy, then end-to-end probes.
  start_svc gateway "$BACKEND" ./app/gateway -f "$ETC_DIR/app/gateway/etc/gateway.yaml" || return $?
  frontend_up || return $?
  proxy_up || return $?
  wait_port 127.0.0.1 "$GATEWAY_PORT" 240 gateway || return $?
  wait_port 127.0.0.1 "$FRONT_PORT" 240 frontend || return $?
  wait_http "http://127.0.0.1:$ENTRY_PORT/" 60 proxy-entry || return $?

  # Data catch-up and a final liveness sweep before reporting the entry URL.
  maybe_rebuild_search || return $?
  validate_all_app_processes || return $?
  echo "entry http://127.0.0.1:$ENTRY_PORT/  (page=$(http_code "http://127.0.0.1:$ENTRY_PORT/") api=$(http_code "http://127.0.0.1:$ENTRY_PORT/api/v1/"))"
}

# Stops, newest first, exactly what this app-up started; keeps the first error.
rollback_app_starts() {
  local index name step_status status=0
  for ((index = ${#APP_UP_STARTED_SERVICES[@]} - 1; index >= 0; index--)); do
    name="${APP_UP_STARTED_SERVICES[index]}"
    if [[ "$name" == "proxy" ]]; then
      if proxy_down; then
        :
      else
        step_status=$?
        [[ "$status" -ne 0 ]] || status="$step_status"
      fi
    elif stop_svc "$name"; then
      :
    else
      step_status=$?
      [[ "$status" -ne 0 ]] || status="$step_status"
    fi
  done
  return "$status"
}

# All-or-nothing app-up: on any failure, roll back what this run started.
app_up_locked() {
  local status rollback_status=0
  APP_UP_STARTED_SERVICES=()
  APP_UP_TRACK_STARTS=1
  if app_up_steps; then
    APP_UP_TRACK_STARTS=0
    return 0
  else
    status=$?
  fi
  APP_UP_TRACK_STARTS=0

  echo "application startup failed with status $status; rolling back" >&2
  rollback_app_starts || rollback_status=$?
  if [[ "$rollback_status" -ne 0 ]]; then
    echo "application rollback also failed with status $rollback_status" >&2
  fi
  return "$status"
}

app_up() {
  with_app_lifecycle_lock exclusive app_up_locked
}

# Stops every app in reverse start order, then frees the frontend and gateway
# ports. Their owner fences are captured first because stop_svc removes the
# pidfiles that would otherwise prove ownership during the port fallback.
app_down_locked() {
  local name step_status status=0
  local frontend_fence_mode frontend_fence_token gateway_fence_mode gateway_fence_token
  capture_service_stop_fence frontend frontend_fence_mode frontend_fence_token || return $?
  capture_service_stop_fence gateway gateway_fence_mode gateway_fence_token || return $?
  while IFS= read -r name; do
    if stop_svc "$name"; then
      :
    else
      step_status=$?
      [[ "$status" -ne 0 ]] || status="$step_status"
    fi
  done < <(all_app_names | tac)
  if proxy_down; then
    :
  else
    step_status=$?
    [[ "$status" -ne 0 ]] || status="$step_status"
  fi
  if stop_owned_port frontend "$FRONT_PORT" "$frontend_fence_mode" "$frontend_fence_token"; then
    :
  else
    step_status=$?
    if ! restore_service_stop_fence frontend "$frontend_fence_mode" "$frontend_fence_token"; then
      echo "failed to restore frontend owner fence after port cleanup failure" >&2
    fi
    [[ "$status" -ne 0 ]] || status="$step_status"
  fi
  if stop_owned_port gateway "$GATEWAY_PORT" "$gateway_fence_mode" "$gateway_fence_token"; then
    :
  else
    step_status=$?
    if ! restore_service_stop_fence gateway "$gateway_fence_mode" "$gateway_fence_token"; then
      echo "failed to restore gateway owner fence after port cleanup failure" >&2
    fi
    [[ "$status" -ne 0 ]] || status="$step_status"
  fi
  return "$status"
}

app_down() {
  with_app_lifecycle_lock exclusive app_down_locked
}

# One status line per app: no-pid, alive (agent: ready/UNREADY), ORPHAN group
# or stale-pid.
pid_state() {
  local name="$1"
  local pidfile="$PID_DIR/$name.pid"
  if [[ ! -e "$pidfile" && ! -L "$pidfile" ]]; then
    printf '%-18s %s\n' "$name" "no-pid"
    return
  fi
  local pid token
  if pid="$(validated_service_pid "$name" "$pidfile")"; then
    if [[ "$name" == "assistant-agent" ]]; then
      token="$(read_service_owner_token "$name" "$pidfile" 2>/dev/null || true)"
      if [[ -n "$token" ]] && normalize_assistant_agent_metrics_port &&
        assistant_agent_ready_matches "$pid" "$token"; then
        printf '%-18s alive ready pid=%s\n' "$name" "$pid"
      else
        printf '%-18s alive UNREADY pid=%s\n' "$name" "$pid"
      fi
    else
      printf '%-18s alive pid=%s\n' "$name" "$pid"
    fi
  elif pid="$(read_service_pidfile "$pidfile" 2>/dev/null)" &&
    managed_process_group_matches "$name" "$pid" "$pidfile"; then
    printf '%-18s ORPHAN group=%s\n' "$name" "$pid"
  else
    printf '%-18s %s\n' "$name" "stale-pid"
  fi
}

# open/closed for an optional algorithm service port.
algorithm_port_state() {
  local port="$1"
  if port_open 127.0.0.1 "$port"; then
    printf 'open'
  else
    printf 'closed'
  fi
}

stack_status_locked() {
  normalize_assistant_agent_metrics_port || return $?
  echo "== containers =="
  compose ps --format 'table {{.Name}}\t{{.Service}}\t{{.Status}}' || true
  if docker inspect "$PROXY_NAME" >/dev/null 2>&1; then
    echo "$PROXY_NAME $(docker inspect -f '{{.State.Status}}' "$PROXY_NAME")"
  else
    echo "$PROXY_NAME absent"
  fi
  echo
  echo "== algorithm =="
  COMPOSE_PROFILES=algorithm compose ps \
    --format 'table {{.Name}}\t{{.Service}}\t{{.Status}}' \
    embedding-service online-infer moderation-infer || true
  echo
  echo "== app pids =="
  local name
  while IFS= read -r name; do
    pid_state "$name"
  done < <(all_app_names)
  echo
  echo "== ports =="
  printf ':%s nginx  %s\n' "$ENTRY_PORT" "$(http_code "http://127.0.0.1:$ENTRY_PORT/")"
  printf ':%s api    %s\n' "$ENTRY_PORT" "$(http_code "http://127.0.0.1:$ENTRY_PORT/api/v1/")"
  printf ':%s gw     %s\n' "$GATEWAY_PORT" "$(http_code "http://127.0.0.1:$GATEWAY_PORT/")"
  printf ':%s front  %s\n' "$FRONT_PORT" "$(http_code "http://127.0.0.1:$FRONT_PORT/")"
  printf ':%s loki  %s\n' "$LOKI_PORT" "$(http_code "http://127.0.0.1:$LOKI_PORT/ready")"
  printf ':%s agent %s\n' "$ASSISTANT_AGENT_METRICS_PORT" \
    "$(http_code "http://127.0.0.1:$ASSISTANT_AGENT_METRICS_PORT/metrics")"
  printf ':%s embed %s\n' "$EMBEDDING_PORT" "$(algorithm_port_state "$EMBEDDING_PORT")"
  printf ':%s infer  %s\n' "$ONLINE_INFER_PORT" "$(algorithm_port_state "$ONLINE_INFER_PORT")"
  printf ':%s modinf %s\n' "$MODERATION_INFER_PORT" "$(algorithm_port_state "$MODERATION_INFER_PORT")"
}

stack_status() {
  with_app_lifecycle_lock shared stack_status_locked
}

# Full up: stop old apps, migrate middleware, start apps from the same source.
stack_up_locked() {
  app_down_locked || return $?
  middleware_up_locked || return $?
  app_up_locked || return $?
  stack_status_locked || return $?
}

stack_up() {
  with_app_lifecycle_lock exclusive stack_up_locked
}

stack_down_locked() {
  app_down_locked || return $?
  algorithm_down_locked || return $?
  middleware_down_locked || return $?
  echo "stopped"
}

stack_down() {
  with_app_lifecycle_lock exclusive stack_down_locked
}

stack_restart_locked() {
  # Bounce app + default middleware only. Algorithm profile containers stay
  # up across restart so the next up does not re-download model weights.
  app_down_locked || return $?
  middleware_down_locked || return $?
  stack_up_locked || return $?
}

stack_restart() {
  with_app_lifecycle_lock exclusive stack_restart_locked
}
