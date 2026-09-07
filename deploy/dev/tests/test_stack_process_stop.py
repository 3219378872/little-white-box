import os
from pathlib import Path
import signal
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest

from deploy.dev.tests.stack_support import (
    ROOT,
    STACK,
    run_bash,
    stop_test_process,
    wait_for_path,
    process_is_running,
)


class StackProcessStopTest(unittest.TestCase):
    def test_stop_service_does_not_signal_reused_pid(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            run_dir = temp / "run"
            pid_dir = run_dir / "pids"
            pid_dir.mkdir(parents=True)
            sleeper = subprocess.Popen(
                [shutil.which("sleep") or "sleep", "300"],
                start_new_session=True,
            )
            try:
                pidfile = pid_dir / "gateway.pid"
                pidfile.write_text(f"{sleeper.pid}\n", encoding="ascii")
                script = f"""
export ROOT={shlex.quote(str(ROOT))}
export RUN_DIR={shlex.quote(str(run_dir))}
export LOG_DIR={shlex.quote(str(run_dir / 'logs'))}
export PID_DIR={shlex.quote(str(pid_dir))}
source {shlex.quote(str(STACK))}
stop_svc gateway
"""
                result = run_bash(script)

                self.assertIsNone(sleeper.poll())
                self.assertFalse(pidfile.exists())
                self.assertIn("removing stale pidfile for gateway", result.stderr)
            finally:
                stop_test_process(sleeper)

    @unittest.skipUnless(Path("/proc/self/stat").exists(), "requires procfs")
    def test_stop_service_recovers_owned_group_after_leader_exit(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            pid_dir = temp / "pids"
            pid_dir.mkdir()
            child_pid_file = temp / "child.pid"
            leader_script = temp / "orphan-group.sh"
            leader_script.write_text(
                "#!/usr/bin/env bash\n"
                "sleep 300 &\n"
                f"printf '%s\\n' \"$!\" >{shlex.quote(str(child_pid_file))}\n",
                encoding="utf-8",
            )
            leader_script.chmod(0o700)
            token = "gateway:orphan-group:test:1"
            child_pid = None
            environment = os.environ.copy()
            environment["XBH_STACK_PROCESS_TOKEN"] = token
            leader = subprocess.Popen(
                [str(leader_script)],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                leader.wait(timeout=3)
                wait_for_path(child_pid_file)
                child_pid = int(child_pid_file.read_text(encoding="ascii").strip())
                self.assertTrue(process_is_running(child_pid))

                pidfile = pid_dir / "gateway.pid"
                owner = Path(f"{pidfile}.owner")
                pidfile.write_text(f"{leader.pid}\n", encoding="ascii")
                owner.write_text(f"{token}\n", encoding="ascii")
                script = f"""
export ROOT={shlex.quote(str(ROOT))}
export PID_DIR={shlex.quote(str(pid_dir))}
source {shlex.quote(str(STACK))}
stop_svc gateway
"""
                result = run_bash(script)

                self.assertIn("stopping orphaned gateway", result.stdout)
                self.assertFalse(pidfile.exists())
                self.assertFalse(owner.exists())
                deadline = time.monotonic() + 3
                while process_is_running(child_pid) and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertFalse(process_is_running(child_pid))
            finally:
                try:
                    os.killpg(leader.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_stop_service_failure_keeps_pidfile_and_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            pid_dir = temp / "pids"
            pid_dir.mkdir()
            pidfile = pid_dir / "gateway.pid"
            pidfile.write_text("4242\n", encoding="ascii")
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export PID_DIR={shlex.quote(str(pid_dir))}
source {shlex.quote(str(STACK))}
validated_service_pid() {{ printf '4242\n'; }}
service_process_matches() {{ return 0; }}
kill() {{ return 1; }}
sleep() {{ return 0; }}
stop_svc gateway
"""
            result = run_bash(script, check=False)

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(pidfile.exists())
            self.assertEqual(pidfile.read_text(encoding="ascii"), "4242\n")
            self.assertIn("failed to send TERM", result.stderr)
            self.assertIn("keeping pidfile", result.stderr)

    def test_stop_tree_does_not_escalate_after_owner_token_changes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            events = Path(tmp_dir) / "events"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export TEST_EVENTS={shlex.quote(str(events))}
source {shlex.quote(str(STACK))}
checks=0
service_process_can_be_stopped() {{
  checks=$((checks + 1))
  [[ "$checks" -eq 1 ]]
}}
service_process_matches() {{ return 0; }}
process_group_running() {{ return 1; }}
kill() {{ builtin printf '%s\n' "$*" >>"$TEST_EVENTS"; }}
sleep() {{ return 0; }}
set +e
stop_tree gateway 4242
status=$?
set -e
builtin printf 'status=%s\n' "$status"
"""
            result = run_bash(script)

            self.assertIn("status=1", result.stdout)
            self.assertEqual(events.read_text(encoding="utf-8").splitlines(), ["4242"])
            self.assertIn("owner token changed", result.stderr)

    def test_process_group_stop_does_not_escalate_after_token_changes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            pid_dir = temp / "pids"
            pid_dir.mkdir()
            (pid_dir / "gateway.pid.owner").write_text(
                "gateway:expected:token:1\n", encoding="ascii"
            )
            events = temp / "events"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export PID_DIR={shlex.quote(str(pid_dir))}
export TEST_EVENTS={shlex.quote(str(events))}
source {shlex.quote(str(STACK))}
token_checks=0
process_group_running() {{ return 0; }}
process_group_has_owner_token() {{
  token_checks=$((token_checks + 1))
  [[ "$token_checks" -eq 1 ]]
}}
kill() {{ builtin printf '%s\n' "$*" >>"$TEST_EVENTS"; }}
sleep() {{ return 0; }}
set +e
stop_owned_process_group gateway 4242
status=$?
set -e
builtin printf 'status=%s\n' "$status"
"""
            result = run_bash(script)

            self.assertIn("status=1", result.stdout)
            self.assertEqual(
                events.read_text(encoding="utf-8").splitlines(), ["-- -4242"]
            )
            self.assertIn("owner token changed", result.stderr)

    def test_stop_service_propagates_pidfile_removal_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            pidfile = Path(tmp_dir) / "gateway.pid"
            owner = Path(f"{pidfile}.owner")
            pidfile.write_text("4242\n", encoding="ascii")
            owner.write_text("gateway:test:token:1\n", encoding="ascii")
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export PID_DIR={shlex.quote(str(pidfile.parent))}
export TEST_PIDFILE={shlex.quote(str(pidfile))}
source {shlex.quote(str(STACK))}
validated_service_pid() {{ printf '4242\n'; }}
stop_tree() {{ return 0; }}
rm() {{
  target="${{@: -1}}"
  [[ "$target" != "$TEST_PIDFILE" ]] || return 19
  command rm "$@"
}}
stop_svc gateway
"""
            result = run_bash(script, check=False)

            self.assertEqual(result.returncode, 19)
            self.assertTrue(pidfile.exists())
            self.assertEqual(
                owner.read_text(encoding="ascii"), "gateway:test:token:1\n"
            )

    def test_stop_service_removes_orphaned_readiness_state(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            pid_dir = Path(tmp_dir) / "pids"
            pid_dir.mkdir()
            ready = pid_dir / "assistant-agent.pid.ready"
            ready.write_text(
                "4242\nassistant-agent:stale:token:1\n", encoding="ascii"
            )
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export PID_DIR={shlex.quote(str(pid_dir))}
source {shlex.quote(str(STACK))}
stop_svc assistant-agent
"""
            run_bash(script)

            self.assertFalse(ready.exists())

    def test_stop_service_terminates_matching_runtime_binary(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            run_dir = temp / "run"
            bin_dir = run_dir / "bin"
            pid_dir = run_dir / "pids"
            bin_dir.mkdir(parents=True)
            pid_dir.mkdir()
            gateway = bin_dir / "gateway"
            shutil.copy2(shutil.which("sleep") or "/bin/sleep", gateway)
            gateway.chmod(0o700)
            process = subprocess.Popen(
                [str(gateway), "300"],
                start_new_session=True,
            )
            try:
                pidfile = pid_dir / "gateway.pid"
                pidfile.write_text(f"{process.pid}\n", encoding="ascii")
                script = f"""
export ROOT={shlex.quote(str(ROOT))}
export RUN_DIR={shlex.quote(str(run_dir))}
export LOG_DIR={shlex.quote(str(run_dir / 'logs'))}
export PID_DIR={shlex.quote(str(pid_dir))}
source {shlex.quote(str(STACK))}
stop_svc gateway
"""
                run_bash(script)

                process.wait(timeout=3)
                self.assertFalse(pidfile.exists())
            finally:
                stop_test_process(process)
