import os
from pathlib import Path
import re
import signal
import shlex
import stat
import tempfile
import unittest

from deploy.dev.tests.stack_support import (
    ROOT,
    STACK,
    run_bash,
    process_is_running,
    stop_test_pid_group,
)


class StackProcessStartTest(unittest.TestCase):
    def test_pid_record_publishes_pid_and_owner_without_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            pidfile = Path(tmp_dir) / "gateway.pid"
            token = "gateway:123:456:test"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
source {shlex.quote(str(STACK))}
record_started_pid gateway 4242 {shlex.quote(str(pidfile))} {shlex.quote(token)}
"""
            run_bash(script)

            owner = Path(f"{pidfile}.owner")
            self.assertEqual(pidfile.read_text(encoding="ascii"), "4242\n")
            self.assertEqual(owner.read_text(encoding="ascii"), f"{token}\n")
            self.assertEqual(stat.S_IMODE(pidfile.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(owner.stat().st_mode), 0o600)
            self.assertEqual(list(Path(tmp_dir).glob("*.tmp.*")), [])

    @unittest.skipUnless(Path("/proc/self/fd").exists(), "requires procfs")
    def test_log_maintainer_does_not_inherit_lifecycle_lock_fd(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            run_dir = temp / "run"
            lock_path = temp / "app.lock"
            pid = None
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export RUN_DIR={shlex.quote(str(run_dir))}
export LOG_DIR={shlex.quote(str(run_dir / 'logs'))}
export PID_DIR={shlex.quote(str(run_dir / 'pids'))}
export ETC_DIR={shlex.quote(str(temp / 'etc'))}
export APP_LIFECYCLE_LOCK={shlex.quote(str(lock_path))}
export LOG_ROTATE_INTERVAL_SECONDS=300
source {shlex.quote(str(STACK))}
with_app_lifecycle_lock exclusive start_log_maintainer
started_pid="$(<{shlex.quote(str(run_dir / 'pids' / 'log-maintainer.pid'))})"
builtin printf 'pid=%s\n' "$started_pid"
"""
            try:
                result = run_bash(script)
                match = re.search(r"^pid=([0-9]+)$", result.stdout, re.MULTILINE)
                self.assertIsNotNone(match, result.stdout)
                pid = int(match.group(1))
                self.assertTrue(process_is_running(pid))

                targets = []
                for descriptor in Path(f"/proc/{pid}/fd").iterdir():
                    try:
                        targets.append(os.readlink(descriptor))
                    except FileNotFoundError:
                        continue
                self.assertNotIn(str(lock_path), targets)
            finally:
                if pid is not None:
                    stop_test_pid_group(pid)

    def test_pidfile_failure_terminates_newly_started_process(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            pidfile = temp / "service.pid"
            captured_pid = temp / "started.pid"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
source {shlex.quote(str(STACK))}
token="test-service:$BASHPID:$RANDOM:test"
env "$MANAGED_PROCESS_TOKEN_ENV=$token" setsid sleep 300 &
started_pid=$!
printf '%s\n' "$started_pid" >{shlex.quote(str(captured_pid))}
chmod() {{ return 23; }}
set +e
record_started_pid test-service "$started_pid" {shlex.quote(str(pidfile))} "$token"
status=$?
set -e
if kill -0 "$started_pid" 2>/dev/null; then alive=1; else alive=0; fi
printf 'status=%s alive=%s\n' "$status" "$alive"
"""
            try:
                result = run_bash(script)
                self.assertIn("status=23 alive=0", result.stdout)
                self.assertFalse(pidfile.exists())
                self.assertIn("stopping the newly started process", result.stderr)
            finally:
                if captured_pid.exists():
                    pid = int(captured_pid.read_text(encoding="ascii").strip())
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_stop_started_tree_tracks_a_late_setsid_transition(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            events = Path(tmp_dir) / "events"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export TEST_EVENTS={shlex.quote(str(events))}
source {shlex.quote(str(STACK))}
group_checks=0
process_group_running() {{
  group_checks=$((group_checks + 1))
  [[ "$group_checks" -eq 2 ]]
}}
process_pid_running() {{ return 0; }}
started_process_can_be_stopped() {{ return 0; }}
kill() {{
  printf '%s\n' "$*" >>"$TEST_EVENTS"
  [[ "$1" != -0 ]]
}}
sleep() {{ return 0; }}
stop_started_tree transitioning 4242
"""
            run_bash(script)
            recorded = events.read_text(encoding="utf-8").splitlines()

            self.assertEqual(recorded[:2], ["4242", "-- -4242"])

    def test_stop_started_tree_escalates_for_a_surviving_process_group(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            tree = temp / "stubborn-tree.sh"
            tree.write_text(
                "#!/usr/bin/env bash\n"
                "trap 'exit 0' TERM\n"
                "bash -c 'trap \"\" TERM; while :; do sleep 1; done' &\n"
                "wait\n",
                encoding="utf-8",
            )
            tree.chmod(0o700)
            captured_pid = temp / "tree.pid"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
source {shlex.quote(str(STACK))}
setsid {shlex.quote(str(tree))} &
started_pid=$!
printf '%s\n' "$started_pid" >{shlex.quote(str(captured_pid))}
sleep 0.1
stop_started_tree stubborn-tree "$started_pid"
status=$?
if process_group_running "$started_pid"; then group_alive=1; else group_alive=0; fi
printf 'status=%s group_alive=%s\n' "$status" "$group_alive"
"""
            try:
                result = run_bash(script)
                self.assertIn("status=0 group_alive=0", result.stdout)
            finally:
                if captured_pid.exists():
                    pid = int(captured_pid.read_text(encoding="ascii").strip())
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_failed_start_cleanup_keeps_recovery_state_and_status(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            pidfile = Path(tmp_dir) / "gateway.pid"
            owner = Path(f"{pidfile}.owner")
            pidfile.write_text("4242\n", encoding="ascii")
            owner.write_text("gateway:test:token:1\n", encoding="ascii")
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
source {shlex.quote(str(STACK))}
stop_started_tree() {{ return 37; }}
remove_service_state() {{ printf '%s\n' removed; return 0; }}
set +e
cleanup_failed_service_start gateway 4242 {shlex.quote(str(pidfile))}
status=$?
set -e
printf 'status=%s\n' "$status"
"""
            result = run_bash(script)

            self.assertIn("status=37", result.stdout)
            self.assertNotIn("removed", result.stdout)
            self.assertEqual(pidfile.read_text(encoding="ascii"), "4242\n")
            self.assertEqual(
                owner.read_text(encoding="ascii"), "gateway:test:token:1\n"
            )
            self.assertIn("keeping pidfile", result.stderr)
