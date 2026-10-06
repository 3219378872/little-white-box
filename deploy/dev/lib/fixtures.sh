# shellcheck shell=bash
# Loaded by ../stack.sh; functions share the stack namespace.
#
# Deterministic assistant gates: temporarily point the running assistant agent
# at the local LLM/search fixture (e2e/fixtures/llm_provider.py), run the
# selected e2e scenario, then restore the agent with its real configuration.

# Prints the assistant-agent row from MQ_SERVICES.
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

# Undoes the fixture swap (also used as the EXIT trap): stop the fixture-bound
# agent and the fixture, then start the agent with the real env. Restarting is
# skipped if the agent could not be stopped; the first error is returned.
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

# Restarts the agent against the fixture for SCENARIO (reset: stream reset and
# replay; research: structured research with fixture search) and runs the
# matching e2e tests. The fixture env lives only in the start subshell.
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

# Runs one fixture SCENARIO end to end; see the header for the sequence.
e2e_agent_reset_locked() {
  local scenario="${1:-reset}"
  load_env || return $?
  ensure_assistant_db_env || return $?
  secure_runtime_paths || return $?
  local agent_row fixture_pidfile fixture_log
  local test_status=0 restore_status=0
  agent_row="$(assistant_agent_row)" || return $?

  # The gate swaps a running agent; it never starts the stack by itself.
  if ! validated_service_pid assistant-agent "$PID_DIR/assistant-agent.pid" >/dev/null; then
    echo "assistant-agent must be running before the reset fixture gate" >&2
    return 1
  fi

  # Fresh fixture process with an empty log, healthy before the agent moves.
  stop_svc llm-fixture || return $?
  fixture_pidfile="$PID_DIR/llm-fixture.pid"
  fixture_log="$LOG_DIR/llm-fixture.log"
  : >"$fixture_log" || return $?
  chmod 600 "$fixture_log" || return $?
  launch_managed_process llm-fixture "$fixture_pidfile" "$fixture_log" "$ROOT" -- \
    python3 "$LLM_FIXTURE_SCRIPT" --port "$AGENT_FIXTURE_PORT" --strict || return $?
  if wait_http "http://127.0.0.1:$AGENT_FIXTURE_PORT/health" 30 llm-fixture; then
    :
  else
    test_status=$?
    stop_svc llm-fixture || return $?
    return "$test_status"
  fi

  # From here on the agent must be restored even if the shell exits early.
  AGENT_FIXTURE_RESTORE=1
  trap restore_agent_after_fixture EXIT
  if run_agent_reset_test_with_fixture "$agent_row" "$scenario"; then
    test_status=0
  else
    test_status=$?
  fi
  restore_agent_after_fixture || restore_status=$?
  trap - EXIT
  # A test failure outranks a restore failure in the reported status.
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
