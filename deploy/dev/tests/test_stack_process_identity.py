import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from deploy.dev.tests.stack_support import (
    ROOT,
    STACK,
    run_bash,
    stop_test_process,
    stack_source,
)


class StackProcessIdentityTest(unittest.TestCase):
    def test_canonical_path_fails_when_parent_directory_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing = Path(tmp_dir) / "missing" / "gateway"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
source {shlex.quote(str(STACK))}
canonical_path {shlex.quote(str(missing))}
"""
            result = run_bash(script, check=False)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")

    def test_pid_state_is_read_only_for_mismatched_ownership(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            pid_dir = Path(tmp_dir) / "pids"
            pid_dir.mkdir()
            pidfile = pid_dir / "gateway.pid"
            owner = pid_dir / "gateway.pid.owner"
            pidfile.write_text("99999999\n", encoding="ascii")
            owner.write_text("gateway:stale-token\n", encoding="ascii")
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export PID_DIR={shlex.quote(str(pid_dir))}
source {shlex.quote(str(STACK))}
pid_state gateway
"""
            result = run_bash(script)

            self.assertIn("stale-pid", result.stdout)
            self.assertEqual(pidfile.read_text(encoding="ascii"), "99999999\n")
            self.assertEqual(
                owner.read_text(encoding="ascii"), "gateway:stale-token\n"
            )

    def test_python_service_identity_does_not_depend_on_current_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            log_dir = temp / "logs"
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            fake_python = fake_bin / "python3"
            fake_python.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
            fake_python.chmod(0o700)
            maintainer = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "deploy" / "dev" / "log_maintainer.py"),
                    str(log_dir),
                    "--interval",
                    "300",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                time.sleep(0.05)
                self.assertIsNone(maintainer.poll())
                script = f"""
export ROOT={shlex.quote(str(ROOT))}
export PATH={shlex.quote(str(fake_bin))}:$PATH
source {shlex.quote(str(STACK))}
service_process_matches log-maintainer {maintainer.pid}
"""
                run_bash(script)
                self.assertIsNone(maintainer.poll())
            finally:
                stop_test_process(maintainer)

    def test_non_procfs_fallback_recognizes_python_script_identity(self):
        script_path = ROOT / "deploy" / "dev" / "log_maintainer.py"
        script = f"""
export ROOT={shlex.quote(str(ROOT))}
source {shlex.quote(str(STACK))}
kill() {{ [[ "$1" == -0 ]]; }}
ps() {{
  printf '%s\n' 'python3 {shlex.quote(str(script_path))} /tmp/logs --interval 30'
}}
service_process_matches log-maintainer 99999999
"""
        run_bash(script)

    def test_all_background_launchers_use_checked_pid_recording(self):
        stack = stack_source()

        self.assertEqual(stack.count("record_started_pid "), 4)
        self.assertEqual(stack.count("close_app_lifecycle_lock_fd || exit $?"), 4)
        self.assertEqual(stack.count("cleanup_failed_service_start "), 5)
        for validation in (
            'if ! validated_service_pid "$name" "$pidfile" >/dev/null; then',
            'if ! validated_service_pid log-maintainer "$pidfile" >/dev/null; then',
            'if ! validated_service_pid llm-fixture "$fixture_pidfile" >/dev/null; then',
            'if ! validated_service_pid frontend "$pidfile" >/dev/null; then',
        ):
            self.assertIn(validation, stack)
        self.assertNotIn('echo $! >"$pidfile"', stack)
        self.assertNotIn('echo $! >"$fixture_pidfile"', stack)

    @unittest.skipUnless(Path("/proc/self/environ").exists(), "requires procfs")
    def test_owner_token_mismatch_fences_matching_runtime_binary(self):
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
            process_token = "gateway:process:token:1"
            recorded_token = "gateway:recorded:token:2"
            environment = os.environ.copy()
            environment["XBH_STACK_PROCESS_TOKEN"] = process_token
            process = subprocess.Popen(
                [str(gateway), "300"],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                pidfile = pid_dir / "gateway.pid"
                owner = Path(f"{pidfile}.owner")
                pidfile.write_text(f"{process.pid}\n", encoding="ascii")
                owner.write_text(f"{recorded_token}\n", encoding="ascii")
                script = f"""
export ROOT={shlex.quote(str(ROOT))}
export RUN_DIR={shlex.quote(str(run_dir))}
export LOG_DIR={shlex.quote(str(run_dir / 'logs'))}
export PID_DIR={shlex.quote(str(pid_dir))}
source {shlex.quote(str(STACK))}
stop_svc gateway
"""
                result = run_bash(script, check=False)

                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(process.poll())
                self.assertEqual(pidfile.read_text(encoding="ascii"), f"{process.pid}\n")
                self.assertEqual(owner.read_text(encoding="ascii"), f"{recorded_token}\n")
                self.assertIn("owner token mismatch", result.stderr)
            finally:
                stop_test_process(process)
