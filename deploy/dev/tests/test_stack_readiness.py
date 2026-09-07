from pathlib import Path
import shlex
import shutil
import stat
import tempfile
import unittest

from deploy.dev.tests.stack_support import (
    ROOT,
    STACK,
    run_bash,
    process_is_running,
)


class StackReadinessTest(unittest.TestCase):
    def test_pid_state_distinguishes_agent_readiness(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            pid_dir = Path(tmp_dir) / "pids"
            pid_dir.mkdir()
            (pid_dir / "assistant-agent.pid").write_text("4242\n", encoding="ascii")
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export PID_DIR={shlex.quote(str(pid_dir))}
source {shlex.quote(str(STACK))}
validated_service_pid() {{ builtin printf '%s\n' 4242; }}
read_service_owner_token() {{ builtin printf '%s\n' 'assistant-agent:test:token:1'; }}
assistant_agent_ready_matches() {{ [[ "$TEST_READY" == 1 ]]; }}
TEST_READY=1
pid_state assistant-agent
TEST_READY=0
pid_state assistant-agent
"""
            result = run_bash(script)

            self.assertIn("assistant-agent    alive ready pid=4242", result.stdout)
            self.assertIn("assistant-agent    alive UNREADY pid=4242", result.stdout)

    def test_agent_readiness_waits_for_exact_post_canary_marker(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            pid_dir = temp / "pids"
            pid_dir.mkdir()
            logfile = temp / "assistant-agent.log"
            logfile.write_text(
                "Assistant agent worker starting\n"
                "Assistant agent worker started later\n",
                encoding="utf-8",
            )
            token = "assistant-agent:test:token:1"
            pidfile = pid_dir / "assistant-agent.pid"
            pidfile.write_text("4242\n", encoding="ascii")
            Path(f"{pidfile}.owner").write_text(f"{token}\n", encoding="ascii")
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export PID_DIR={shlex.quote(str(pid_dir))}
export ASSISTANT_AGENT_METRICS_PORT=19136
export TEST_LOG={shlex.quote(str(logfile))}
source {shlex.quote(str(STACK))}
service_process_matches() {{ [[ "$1" == assistant-agent && "$2" == 4242 ]]; }}
process_has_owner_token() {{ [[ "$1" == 4242 && "$2" == {shlex.quote(token)} ]]; }}
listening_port_pids() {{
  [[ "$1" == 19136 ]] || return 81
  builtin printf '%s\n' 4242
}}
sleep_calls=0
sleep() {{
  sleep_calls=$((sleep_calls + 1))
  if [[ "$sleep_calls" -eq 2 ]]; then
    builtin printf '%s\n' "$ASSISTANT_AGENT_READY_LINE" >>"$TEST_LOG"
  fi
}}
wait_assistant_agent_ready 4242 {shlex.quote(token)} "$TEST_LOG" 1 delayed-agent
builtin printf 'sleep_calls=%s\n' "$sleep_calls"
"""
            result = run_bash(script)

            self.assertIn("ready: delayed-agent", result.stdout)
            self.assertIn("sleep_calls=2", result.stdout)
            ready = Path(f"{pidfile}.ready")
            self.assertEqual(ready.read_text(encoding="ascii"), f"4242\n{token}\n")
            self.assertEqual(stat.S_IMODE(ready.stat().st_mode), 0o600)

    def test_agent_readiness_fails_closed_when_owner_token_changes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            pid_dir = temp / "pids"
            pid_dir.mkdir()
            logfile = temp / "assistant-agent.log"
            logfile.write_text("not ready yet\n", encoding="utf-8")
            token = "assistant-agent:test:token:1"
            pidfile = pid_dir / "assistant-agent.pid"
            owner = Path(f"{pidfile}.owner")
            pidfile.write_text("4242\n", encoding="ascii")
            owner.write_text(f"{token}\n", encoding="ascii")
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export PID_DIR={shlex.quote(str(pid_dir))}
export TEST_LOG={shlex.quote(str(logfile))}
export TEST_OWNER={shlex.quote(str(owner))}
source {shlex.quote(str(STACK))}
service_process_matches() {{ return 0; }}
process_has_owner_token() {{ [[ "$2" == {shlex.quote(token)} ]]; }}
sleep() {{
  builtin printf '%s\n' 'assistant-agent:replacement:token:2' >"$TEST_OWNER"
  builtin printf '%s\n' "$ASSISTANT_AGENT_READY_LINE" >>"$TEST_LOG"
}}
set +e
wait_assistant_agent_ready 4242 {shlex.quote(token)} "$TEST_LOG" 1 changed-agent
status=$?
set -e
builtin printf 'status=%s\n' "$status"
"""
            result = run_bash(script)

            self.assertIn("status=1", result.stdout)
            self.assertIn("exited or changed identity", result.stderr)
            self.assertFalse(Path(f"{pidfile}.ready").exists())

    def test_agent_readiness_rejects_foreign_metrics_listener_immediately(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            pid_dir = temp / "pids"
            pid_dir.mkdir()
            logfile = temp / "assistant-agent.log"
            logfile.write_text(
                "Assistant agent worker started\n", encoding="utf-8"
            )
            token = "assistant-agent:test:token:1"
            pidfile = pid_dir / "assistant-agent.pid"
            pidfile.write_text("4242\n", encoding="ascii")
            Path(f"{pidfile}.owner").write_text(f"{token}\n", encoding="ascii")
            slept = temp / "slept"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export PID_DIR={shlex.quote(str(pid_dir))}
export TEST_SLEPT={shlex.quote(str(slept))}
source {shlex.quote(str(STACK))}
service_process_matches() {{ return 0; }}
process_has_owner_token() {{ return 0; }}
listening_port_pids() {{ builtin printf '%s\n' 9999; }}
port_open() {{ return 0; }}
sleep() {{ : >"$TEST_SLEPT"; }}
set +e
wait_assistant_agent_ready 4242 {shlex.quote(token)} {shlex.quote(str(logfile))} 1 foreign-agent
status=$?
set -e
builtin printf 'status=%s\n' "$status"
"""
            result = run_bash(script)

            self.assertIn("status=1", result.stdout)
            self.assertIn("metrics listener", result.stderr)
            self.assertFalse(slept.exists())
            self.assertFalse(Path(f"{pidfile}.ready").exists())

    def test_agent_readiness_retries_an_empty_listener_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            logfile = Path(tmp_dir) / "assistant-agent.log"
            logfile.write_text(
                "Assistant agent worker started\n", encoding="utf-8"
            )
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
source {shlex.quote(str(STACK))}
assistant_agent_launch_matches() {{ return 0; }}
listener_checks=0
listening_port_owner_state() {{
  listener_checks=$((listener_checks + 1))
  [[ "$listener_checks" -gt 1 ]]
}}
record_assistant_agent_ready() {{ return 0; }}
assistant_agent_ready_matches() {{ return 0; }}
sleep_calls=0
sleep() {{ sleep_calls=$((sleep_calls + 1)); }}
wait_assistant_agent_ready 4242 'assistant-agent:test:token:1' {shlex.quote(str(logfile))} 1
builtin printf 'listener_checks=%s sleep_calls=%s\n' "$listener_checks" "$sleep_calls"
"""
            result = run_bash(script)

            self.assertIn("ready: assistant-agent", result.stdout)
            self.assertIn("listener_checks=2 sleep_calls=1", result.stdout)

    def test_agent_readiness_propagates_poll_sleep_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            logfile = Path(tmp_dir) / "assistant-agent.log"
            logfile.write_text("not ready\n", encoding="utf-8")
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
source {shlex.quote(str(STACK))}
assistant_agent_launch_matches() {{ return 0; }}
sleep() {{ return 77; }}
set +e
wait_assistant_agent_ready 4242 'assistant-agent:test:token:1' {shlex.quote(str(logfile))} 1
status=$?
set -e
builtin printf 'status=%s\n' "$status"
"""
            result = run_bash(script)

            self.assertIn("status=77", result.stdout)

    def test_agent_already_running_requires_persisted_readiness(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            run_dir = temp / "run"
            events = temp / "events"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export RUN_DIR={shlex.quote(str(run_dir))}
export LOG_DIR={shlex.quote(str(run_dir / 'logs'))}
export PID_DIR={shlex.quote(str(run_dir / 'pids'))}
export ETC_DIR={shlex.quote(str(temp / 'etc'))}
export TEST_EVENTS={shlex.quote(str(events))}
source {shlex.quote(str(STACK))}
validated_service_pid() {{ builtin printf '%s\n' 4242; }}
read_service_owner_token() {{ builtin printf '%s\n' 'assistant-agent:test:token:1'; }}
assistant_agent_ready_matches() {{
  builtin printf 'ready-check:%s:%s\n' "$1" "$2" >>"$TEST_EVENTS"
  [[ "$TEST_READY" == 1 ]]
}}
go() {{ builtin printf '%s\n' build >>"$TEST_EVENTS"; return 91; }}
TEST_READY=0
set +e
start_svc assistant-agent {shlex.quote(str(temp))} ./unused
missing_status=$?
set -e
TEST_READY=1
start_svc assistant-agent {shlex.quote(str(temp))} ./unused
builtin printf 'missing=%s\n' "$missing_status"
"""
            result = run_bash(script)

            self.assertIn("missing=1", result.stdout)
            self.assertIn("already running: assistant-agent pid=4242", result.stdout)
            self.assertIn("without verified post-canary readiness", result.stderr)
            self.assertEqual(
                events.read_text(encoding="utf-8").splitlines(),
                [
                    "ready-check:4242:assistant-agent:test:token:1",
                    "ready-check:4242:assistant-agent:test:token:1",
                ],
            )

    @unittest.skipUnless(Path("/proc/self/environ").exists(), "requires procfs")
    def test_agent_readiness_failure_cleans_new_start_and_preserves_status(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            run_dir = temp / "run"
            log_dir = run_dir / "logs"
            log_dir.mkdir(parents=True)
            logfile = log_dir / "assistant-agent.log"
            logfile.write_text(
                "Assistant agent worker started\n", encoding="utf-8"
            )
            rotated = Path(f"{logfile}.1.gz")
            rotated.write_bytes(b"old-sensitive-log")
            observed = temp / "observed"
            sleep_binary = shutil.which("sleep") or "/bin/sleep"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export RUN_DIR={shlex.quote(str(run_dir))}
export LOG_DIR={shlex.quote(str(log_dir))}
export PID_DIR={shlex.quote(str(run_dir / 'pids'))}
export ETC_DIR={shlex.quote(str(temp / 'etc'))}
export TEST_SLEEP_BINARY={shlex.quote(sleep_binary)}
export TEST_OBSERVED={shlex.quote(str(observed))}
source {shlex.quote(str(STACK))}
go() {{
  [[ "$1" == build && "$2" == -o ]] || return 90
  command cp "$TEST_SLEEP_BINARY" "$3"
}}
validated_service_pid() {{
  [[ -f "$2" ]] || return 1
  read_service_pidfile "$2"
}}
new_managed_process_token() {{ builtin printf '%s\n' 'assistant-agent:test:token:1'; }}
wait_assistant_agent_ready() {{
  if grep -Fqx -- "$ASSISTANT_AGENT_READY_LINE" "$3" || [[ -e "$3.1.gz" ]]; then
    return 62
  fi
  builtin printf '%s %s\n' "$1" "$2" >"$TEST_OBSERVED"
  return 63
}}
set +e
start_svc assistant-agent {shlex.quote(str(temp))} ./unused 300
status=$?
set -e
builtin printf 'status=%s\n' "$status"
"""
            result = run_bash(script)

            self.assertIn("status=63", result.stdout)
            self.assertIn("failed post-canary readiness", result.stderr)
            pid_text, observed_token = observed.read_text(encoding="ascii").split()
            self.assertEqual(observed_token, "assistant-agent:test:token:1")
            self.assertFalse(process_is_running(int(pid_text)))
            self.assertFalse((run_dir / "pids" / "assistant-agent.pid").exists())
            self.assertFalse(
                (run_dir / "pids" / "assistant-agent.pid.owner").exists()
            )
            self.assertFalse(
                (run_dir / "pids" / "assistant-agent.pid.ready").exists()
            )
            self.assertEqual(logfile.read_text(encoding="utf-8"), "")
            self.assertFalse(rotated.exists())
