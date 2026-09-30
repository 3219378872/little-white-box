import importlib.util
import os
from pathlib import Path
import shlex
import tempfile
import unittest

from deploy.dev.tests.stack_support import ROOT, STACK, run_bash, unused_loopback_port

spec = importlib.util.spec_from_file_location('front_inputs', ROOT / 'deploy/dev/front_build_inputs.py')
inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs)


def fixture(directory):
    front = directory / 'front'
    for folder in ('lib', 'web'):
        (front / folder).mkdir(parents=True)
    (front / 'lib/main.dart').write_text('synthetic source')
    (front / 'web/index.html').write_text('synthetic source index')
    for name in ('pubspec.yaml', 'pubspec.lock'):
        (front / name).write_text(name)
    flutter = directory / 'sdk/bin/flutter'
    flutter.parent.mkdir(parents=True)
    (flutter.parent / 'cache').mkdir()
    (flutter.parent / 'cache/flutter.version.json').write_text('{"frameworkRevision":"synthetic-sdk-1"}')
    flutter.write_text('''#!/bin/sh
printf 'build\\n' >>"$TEST_BUILDS"
[ "${TEST_FAIL:-0}" = 0 ] || exit "$TEST_FAIL"
[ "${TEST_MUTATE:-0}" = 0 ] || printf 'changed during build' >lib/main.dart
mkdir -p build/web
printf 'SYNTHETIC_BUNDLE' >build/web/index.html
''')
    flutter.chmod(0o700)
    return front, flutter


class FrontBuildInputsTest(unittest.TestCase):
    def test_manifest_detects_add_delete_rename_content_flags_and_sdk(self):
        with tempfile.TemporaryDirectory() as td:
            front, flutter = fixture(Path(td))
            def fingerprint(flags=('build', 'web', '--release')):
                return inputs.fingerprint(front, flutter, list(flags))
            initial = fingerprint()
            self.assertEqual(initial, fingerprint())
            added = front / 'lib/added.dart'
            added.write_text('added')
            after_add = fingerprint()
            self.assertNotEqual(initial, after_add)
            added.rename(front / 'lib/renamed.dart')
            self.assertNotEqual(after_add, fingerprint())
            (front / 'lib/renamed.dart').unlink()
            self.assertEqual(initial, fingerprint())
            source = front / 'lib/main.dart'
            old = source.stat()
            source.write_text('new contents')
            os.utime(source, ns=(old.st_atime_ns, old.st_mtime_ns))
            changed = fingerprint()
            self.assertNotEqual(initial, changed)
            self.assertNotEqual(changed, fingerprint(('build', 'web', '--debug')))
            (flutter.parent / 'cache/flutter.version.json').write_text('{"frameworkRevision":"synthetic-sdk-2"}')
            self.assertNotEqual(changed, fingerprint())

    def test_missing_inputs_and_symlink_cycles_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            front, flutter = fixture(Path(td))
            (front / 'lib/loop').symlink_to(front / 'lib', target_is_directory=True)
            with self.assertRaises(ValueError):
                inputs.fingerprint(front, flutter, [])
            (front / 'lib/loop').unlink()
            (front / 'pubspec.lock').unlink()
            with self.assertRaises(OSError):
                inputs.fingerprint(front, flutter, [])

    def test_force_restarts_running_server_and_failure_leaves_it_stopped(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            front, flutter = fixture(directory)
            run = directory / 'run'
            builds = directory / 'builds'
            result = run_bash(f'''
export ROOT={shlex.quote(str(ROOT))} FRONTEND={shlex.quote(str(front))}
export RUN_DIR={shlex.quote(str(run))} ETC_DIR={shlex.quote(str(directory / 'etc'))}
export FRONT_PORT={unused_loopback_port()} TEST_BUILDS={shlex.quote(str(builds))}
export PATH={shlex.quote(str(flutter.parent))}:$PATH
source {shlex.quote(str(STACK))}
ensure_web_canvaskit() {{ return 0; }}
trap 'stop_svc frontend >/dev/null 2>&1 || true' EXIT
frontend_up
first="$(validated_service_pid frontend "$PID_DIR/frontend.pid")"
frontend_up
[[ "$first" == "$(validated_service_pid frontend "$PID_DIR/frontend.pid")" ]]
stop_svc frontend
frontend_up
first="$(validated_service_pid frontend "$PID_DIR/frontend.pid")"
FORCE_FRONT_BUILD=1 frontend_up
second="$(validated_service_pid frontend "$PID_DIR/frontend.pid")"
[[ "$first" != "$second" ]]
if FORCE_FRONT_BUILD=1 TEST_FAIL=23 frontend_up; then exit 92; else status=$?; fi
[[ "$status" == 23 ]]
[[ ! -e "$PID_DIR/frontend.pid" && ! -e "$RUN_DIR/front-build.stamp" ]]
! process_pid_running "$second"
''')
            self.assertEqual(builds.read_text().splitlines(), ['build'] * 3)
            self.assertIn('already running: frontend', result.stdout)
            self.assertIn('frontend bundle up to date', result.stdout)
            self.assertIn('refusing to serve an existing bundle', result.stderr)

    def test_changed_during_build_cannot_publish_a_current_marker(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            front, flutter = fixture(directory)
            run = directory / 'run'
            result = run_bash(f'''
export ROOT={shlex.quote(str(ROOT))} FRONTEND={shlex.quote(str(front))}
export RUN_DIR={shlex.quote(str(run))} ETC_DIR={shlex.quote(str(directory / 'etc'))}
export TEST_BUILDS={shlex.quote(str(directory / 'builds'))} TEST_MUTATE=1
export PATH={shlex.quote(str(flutter.parent))}:$PATH
source {shlex.quote(str(STACK))}
ensure_web_canvaskit() {{ return 0; }}
frontend_up
''', check=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn('inputs changed during build', result.stderr)
            self.assertFalse((run / 'front-build.stamp').exists())
            self.assertFalse((run / 'pids/frontend.pid').exists())


if __name__ == '__main__':
    unittest.main()
