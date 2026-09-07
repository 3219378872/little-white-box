from pathlib import Path
import shlex
import tempfile
import unittest

from deploy.dev.tests.stack_support import (
    ROOT,
    STACK,
    run_bash,
    stack_source,
)


class StackFixturesTest(unittest.TestCase):
    def test_fixture_restore_reports_cleanup_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            events = Path(tmp_dir) / "events"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export TEST_EVENTS={shlex.quote(str(events))}
source {shlex.quote(str(STACK))}
AGENT_FIXTURE_RESTORE=1
stop_svc() {{
  printf 'stop:%s\n' "$1" >>"$TEST_EVENTS"
  [[ "$1" != llm-fixture ]] || return 47
}}
ensure_assistant_db_env() {{ return 0; }}
assistant_agent_row() {{ printf '%s\n' 'assistant-agent|row'; }}
start_row() {{ printf 'start:%s\n' "${{1%%|*}}" >>"$TEST_EVENTS"; }}
wait_port() {{ printf '%s\n' wait-port >>"$TEST_EVENTS"; }}
set +e
restore_agent_after_fixture
status=$?
set -e
printf 'status=%s restore=%s\n' "$status" "$AGENT_FIXTURE_RESTORE"
"""
            result = run_bash(script)
            recorded = events.read_text(encoding="utf-8").splitlines()

            self.assertIn("status=47 restore=0", result.stdout)
            self.assertEqual(
                recorded,
                ["stop:assistant-agent", "stop:llm-fixture", "start:assistant-agent"],
            )

    def test_agent_fixture_start_delegates_readiness_to_start_row(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            events = Path(tmp_dir) / "events"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export TEST_EVENTS={shlex.quote(str(events))}
source {shlex.quote(str(STACK))}
stop_svc() {{ builtin printf 'stop:%s\n' "$1" >>"$TEST_EVENTS"; }}
start_row() {{ builtin printf 'start:%s\n' "${{1%%|*}}" >>"$TEST_EVENTS"; }}
wait_port() {{ builtin printf '%s\n' wait-port >>"$TEST_EVENTS"; }}
python3() {{ builtin printf 'pytest:%s\n' "$*" >>"$TEST_EVENTS"; }}
run_agent_reset_test_with_fixture 'assistant-agent|row'
"""
            run_bash(script)

            recorded = events.read_text(encoding="utf-8").splitlines()
            self.assertEqual(recorded[0:2], ["stop:assistant-agent", "start:assistant-agent"])
            self.assertTrue(recorded[2].startswith("pytest:-m pytest -v "))
            self.assertNotIn("wait-port", recorded)
            self.assertNotIn(
                "wait_port 127.0.0.1 9136", stack_source()
            )

    def test_agent_reset_restores_inside_lock_and_preserves_setup_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            run_dir = temp / "run"
            events = temp / "events"
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            fake_setsid = fake_bin / "setsid"
            fake_setsid.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_setsid.chmod(0o700)
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export RUN_DIR={shlex.quote(str(run_dir))}
export LOG_DIR={shlex.quote(str(run_dir / 'logs'))}
export PID_DIR={shlex.quote(str(run_dir / 'pids'))}
export ETC_DIR={shlex.quote(str(temp / 'etc'))}
export APP_LIFECYCLE_LOCK={shlex.quote(str(temp / 'app.lock'))}
export TEST_EVENTS={shlex.quote(str(events))}
export PATH={shlex.quote(str(fake_bin))}:$PATH
source {shlex.quote(str(STACK))}
load_env() {{ return 0; }}
ensure_assistant_db_env() {{ return 0; }}
assistant_agent_row() {{ printf '%s\n' 'assistant-agent|row'; }}
validated_service_pid() {{ printf '%s\n' 4242; }}
stop_svc() {{
  printf 'stop:%s\n' "$1" >>"$TEST_EVENTS"
  [[ "$1" != assistant-agent ]] || return 41
}}
new_managed_process_token() {{ printf '%s\n' 'llm-fixture:test:token:1'; }}
record_started_pid() {{ printf '%s\n' record-fixture >>"$TEST_EVENTS"; }}
service_process_matches() {{ return 0; }}
wait_http() {{ return 0; }}
sleep() {{ return 0; }}
restore_agent_after_fixture() {{
  AGENT_FIXTURE_RESTORE=0
  exec {{probe_fd}}>"$APP_LIFECYCLE_LOCK"
  if flock -n "$probe_fd"; then
    printf '%s\n' restore-unlocked >>"$TEST_EVENTS"
    flock -u "$probe_fd"
  else
    printf '%s\n' restore-locked >>"$TEST_EVENTS"
  fi
  exec {{probe_fd}}>&-
}}
set +e
e2e_agent_reset
status=$?
set -e
printf 'status=%s\n' "$status"
"""
            result = run_bash(script)
            recorded = events.read_text(encoding="utf-8").splitlines()

            self.assertIn("status=41", result.stdout)
            self.assertIn("restore-locked", recorded)
            self.assertNotIn("restore-unlocked", recorded)
            self.assertEqual(recorded.count("restore-locked"), 1)
