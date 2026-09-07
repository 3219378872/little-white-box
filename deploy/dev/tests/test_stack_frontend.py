from pathlib import Path
import shlex
import tempfile
import unittest

from deploy.dev.tests.stack_support import (
    ROOT,
    STACK,
    run_bash,
)


class StackFrontendTest(unittest.TestCase):
    def test_front_bundle_fresh_propagates_source_scan_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            frontend = temp / "frontend"
            (frontend / "lib").mkdir(parents=True)
            (frontend / "web").mkdir()
            (frontend / "pubspec.yaml").touch()
            (frontend / "pubspec.lock").touch()
            run_dir = temp / "run"
            run_dir.mkdir()
            (run_dir / "front-build.stamp").touch()
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export FRONTEND={shlex.quote(str(frontend))}
export RUN_DIR={shlex.quote(str(run_dir))}
source {shlex.quote(str(STACK))}
find() {{ return 52; }}
set +e
front_bundle_fresh
status=$?
set -e
builtin printf 'status=%s\n' "$status"
"""
            result = run_bash(script)

            self.assertIn("status=52", result.stdout)

    def test_frontend_build_failure_does_not_serve_existing_bundle(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            frontend = temp / "frontend"
            bundle = frontend / "build" / "web"
            bundle.mkdir(parents=True)
            (bundle / "index.html").write_text("old bundle", encoding="utf-8")

            fake_bin = temp / "bin"
            fake_bin.mkdir()
            flutter = fake_bin / "flutter"
            flutter.write_text("#!/bin/sh\nexit 23\n", encoding="utf-8")
            flutter.chmod(0o700)

            run_dir = temp / "run"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export FRONTEND={shlex.quote(str(frontend))}
export RUN_DIR={shlex.quote(str(run_dir))}
export LOG_DIR={shlex.quote(str(run_dir / 'logs'))}
export PID_DIR={shlex.quote(str(run_dir / 'pids'))}
export ETC_DIR={shlex.quote(str(temp / 'etc'))}
export FORCE_FRONT_BUILD=1
export PATH={shlex.quote(str(fake_bin))}:$PATH
source {shlex.quote(str(STACK))}
setsid() {{ return 0; }}
frontend_up
"""
            result = run_bash(script, check=False)

            self.assertEqual(result.returncode, 23)
            self.assertIn("refusing to serve an existing bundle", result.stderr)
            self.assertFalse((run_dir / "pids" / "frontend.pid").exists())

    def test_proxy_rollback_tracking_uses_previous_running_state(self):
        for existed, running_state, expected_starts in (
            ("0", "false", "proxy"),
            ("1", "false", "proxy"),
            ("1", "true", "none"),
        ):
            with self.subTest(existed=existed, running_state=running_state):
                script = f"""
export ROOT={shlex.quote(str(ROOT))}
export TEST_PROXY_EXISTS={existed}
export TEST_PROXY_RUNNING={running_state}
source {shlex.quote(str(STACK))}
docker() {{
  if [[ "$1" == ps ]]; then
    [[ "$TEST_PROXY_EXISTS" == 0 ]] || builtin printf '%s\n' "$PROXY_NAME"
    return 0
  fi
  if [[ "$1" == inspect && "${{2:-}}" == -f ]]; then
    builtin printf '%s\n' "$TEST_PROXY_RUNNING"
    return 0
  fi
  if [[ "$1" == rm ]]; then
    TEST_PROXY_EXISTS=0
    TEST_PROXY_RUNNING=false
    return 0
  fi
  if [[ "$1" == run ]]; then
    TEST_PROXY_EXISTS=1
    TEST_PROXY_RUNNING=true
    return 0
  fi
  return 0
}}
APP_UP_TRACK_STARTS=1
proxy_up
if [[ ${{#APP_UP_STARTED_SERVICES[@]}} -eq 0 ]]; then
  builtin printf '%s\n' starts=none
else
  builtin printf 'starts=%s\n' "${{APP_UP_STARTED_SERVICES[*]}}"
fi
"""
                result = run_bash(script)

                self.assertIn(f"starts={expected_starts}", result.stdout)

    def test_proxy_up_rejects_container_that_exits_immediately(self):
        script = f"""
export ROOT={shlex.quote(str(ROOT))}
source {shlex.quote(str(STACK))}
docker() {{
  if [[ "$1" == ps ]]; then
    return 0
  fi
  if [[ "$1" == inspect && "${{2:-}}" == -f ]]; then
    builtin printf '%s\n' false
    return 0
  fi
  return 0
}}
APP_UP_TRACK_STARTS=1
set +e
proxy_up
status=$?
set -e
builtin printf 'status=%s starts=%s\n' "$status" "${{APP_UP_STARTED_SERVICES[*]}}"
"""
        result = run_bash(script)

        self.assertIn("status=1 starts=proxy", result.stdout)
        self.assertIn("exited during startup", result.stderr)

    def test_proxy_up_propagates_container_listing_failure(self):
        script = f"""
export ROOT={shlex.quote(str(ROOT))}
source {shlex.quote(str(STACK))}
docker() {{ return 54; }}
set +e
proxy_up
status=$?
set -e
builtin printf 'status=%s\n' "$status"
"""
        result = run_bash(script)

        self.assertIn("status=54", result.stdout)
        self.assertIn("failed to list Docker containers", result.stderr)

    def test_proxy_down_propagates_container_listing_failure(self):
        script = f"""
export ROOT={shlex.quote(str(ROOT))}
source {shlex.quote(str(STACK))}
docker() {{ return 53; }}
set +e
proxy_down
status=$?
set -e
builtin printf 'status=%s\n' "$status"
"""
        result = run_bash(script)

        self.assertIn("status=53", result.stdout)
        self.assertIn("failed to list Docker containers", result.stderr)
