# shellcheck shell=bash
# Loaded by ../stack.sh; functions share the stack namespace.

assistant_agent_row() {
  local row
  for row in "${MQ_SERVICES[@]}"; do
    if [[ "$row" == assistant-agent\|* ]]; then
      printf '%s\n' "$row"
      return 0
    fi
  done
  echo "assistant-agent service row is missing" >&2
  return 1
}

restore_agent_after_fixture() {
  [[ "$AGENT_FIXTURE_RESTORE" == "1" ]] || return 0
  AGENT_FIXTURE_RESTORE=0
  local restore_status=0 step_status=0 can_start=1 row
  if stop_svc assistant-agent; then
    :
  else
    restore_status=$?
    can_start=0
  fi
  if stop_svc llm-fixture; then
    :
  else
    step_status=$?
    [[ "$restore_status" -ne 0 ]] || restore_status="$step_status"
  fi
  if ensure_assistant_db_env; then
    :
  else
    step_status=$?
    [[ "$restore_status" -ne 0 ]] || restore_status="$step_status"
    can_start=0
  fi
  if [[ "$can_start" == "1" ]] && row="$(assistant_agent_row)"; then
    if ! start_row "$row"; then
      echo "failed to restore assistant-agent; run just app-up" >&2
      [[ "$restore_status" -ne 0 ]] || restore_status=1
    fi
  elif [[ "$can_start" == "1" ]]; then
    [[ "$restore_status" -ne 0 ]] || restore_status=1
  else
    echo "skipping assistant-agent restart because fixture cleanup failed" >&2
  fi
  return "$restore_status"
}

run_agent_reset_test_with_fixture() {
  local agent_row="$1" scenario="${2:-reset}" test_path reset_flag=0 research_flag=0
  case "$scenario" in
    reset) test_path="$ROOT/deploy/dev/e2e/test_assistant.py::test_agent_stream_reset_replay"; reset_flag=1 ;;
    research) test_path="$ROOT/deploy/dev/e2e/test_assistant_research.py"; research_flag=1 ;;
    *) echo "unsupported assistant fixture scenario" >&2; return 2 ;;
  esac
  stop_svc assistant-agent || return $?
  (
    export ASSISTANT_LLM_ENABLED=true
    export ASSISTANT_LLM_WIRE_API=responses
    export ASSISTANT_LLM_ENDPOINT="http://127.0.0.1:$AGENT_FIXTURE_PORT/v1"
    export ASSISTANT_LLM_API_KEY=""
    export ASSISTANT_LLM_MODEL=fixture-model
    export ASSISTANT_LLM_MODEL_SMALL=""
    export ASSISTANT_LLM_REVIEW_MODEL=""
    export ASSISTANT_LLM_PROMPT_COST_PER_MILLION_TOKENS=0
    export ASSISTANT_LLM_COMPLETION_COST_PER_MILLION_TOKENS=0
    export ASSISTANT_LLM_CACHE_READ_COST_PER_MILLION_TOKENS=0
    export ASSISTANT_LLM_CACHE_WRITE_COST_PER_MILLION_TOKENS=0
    export ASSISTANT_LLM_REASONING_COST_PER_MILLION_TOKENS=0
    export ASSISTANT_LLM_FALLBACK_ENABLED=false
    if [[ "$scenario" == research ]]; then
      export TAVILY_API_KEY=fixture-only
      export TAVILY_ENDPOINT="http://127.0.0.1:$AGENT_FIXTURE_PORT"
    fi
    start_row "$agent_row"
  ) || return $?
  E2E_EXPECT_ASSISTANT_RESET="$reset_flag" E2E_EXPECT_ASSISTANT_RESEARCH="$research_flag" PYTHONDONTWRITEBYTECODE=1 \
    python3 -m pytest -v \
    "$test_path"
}

e2e_agent_reset_locked() {
  local scenario="${1:-reset}"
  load_env || return $?
  ensure_assistant_db_env || return $?
  secure_runtime_paths || return $?
  local agent_row fixture_pidfile fixture_log fixture_pid fixture_token agent_pidfile agent_pid
  local test_status=0 restore_status=0 cleanup_status=0
  agent_row="$(assistant_agent_row)" || return $?
  agent_pidfile="$PID_DIR/assistant-agent.pid"
  if ! agent_pid="$(validated_service_pid assistant-agent "$agent_pidfile")"; then
    echo "assistant-agent must be running before the reset fixture gate" >&2
    return 1
  fi

  stop_svc llm-fixture || return $?
  fixture_pidfile="$PID_DIR/llm-fixture.pid"
  fixture_log="$LOG_DIR/llm-fixture.log"
  : >"$fixture_log" || return $?
  chmod 600 "$fixture_log" || return $?
  fixture_token="$(new_managed_process_token llm-fixture)" || return $?
  (
    close_app_lifecycle_lock_fd || exit $?
    exec env "$MANAGED_PROCESS_TOKEN_ENV=$fixture_token" \
      setsid python3 "$ROOT/deploy/dev/e2e/fixtures/llm_provider.py" \
      --port "$AGENT_FIXTURE_PORT" --strict
  ) >>"$fixture_log" 2>&1 </dev/null &
  fixture_pid=$!
  record_started_pid llm-fixture "$fixture_pid" "$fixture_pidfile" "$fixture_token" || return $?
  sleep 0.1
  if ! validated_service_pid llm-fixture "$fixture_pidfile" >/dev/null; then
    if cleanup_failed_service_start llm-fixture "$fixture_pid" "$fixture_pidfile" "$fixture_token"; then
      :
    else
      cleanup_status=$?
    fi
    echo "llm fixture exited during startup; see $fixture_log" >&2
    [[ "$cleanup_status" -eq 0 ]] || return "$cleanup_status"
    return 1
  fi
  if wait_http "http://127.0.0.1:$AGENT_FIXTURE_PORT/health" 30 llm-fixture; then
    :
  else
    test_status=$?
    stop_svc llm-fixture || return $?
    return "$test_status"
  fi

  AGENT_FIXTURE_RESTORE=1
  trap restore_agent_after_fixture EXIT
  if run_agent_reset_test_with_fixture "$agent_row" "$scenario"; then
    test_status=0
  else
    test_status=$?
  fi
  restore_agent_after_fixture || restore_status=$?
  trap - EXIT
  if [[ "$test_status" -ne 0 ]]; then
    return "$test_status"
  fi
  return "$restore_status"
}

e2e_agent_reset() {
  with_app_lifecycle_lock exclusive e2e_agent_reset_locked
}

e2e_agent_research() {
  with_app_lifecycle_lock exclusive e2e_agent_reset_locked research
}

