"""Deterministic synthetic-only regression tests for log cleanup boundaries."""
import importlib.util
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from deploy.dev.tests.stack_support import ROOT, STACK, run_bash, wait_for_path

SCRIPT = ROOT / 'deploy/dev/log_maintainer.py'
spec = importlib.util.spec_from_file_location('log_lifecycle', SCRIPT)
logs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logs)


class LogLifecycleTest(unittest.TestCase):
    def test_cleanup_waits_for_publish_and_truncate_windows(self):
        for window in ('publish', 'truncate'):
            with self.subTest(window=window), tempfile.TemporaryDirectory() as td:
                directory = Path(td)
                path = directory / 'assistant-agent.log'
                path.write_text('SYNTHETIC_OLD_SENTINEL' * 100)
                reached, release = threading.Event(), threading.Event()
                real_replace, real_chmod = logs.os.replace, logs.os.chmod

                def pause():
                    reached.set()
                    self.assertTrue(release.wait(5))

                def replace(source, target):
                    if window == 'publish':
                        pause()
                    return real_replace(source, target)

                def chmod(target, mode):
                    if window == 'truncate' and str(target).endswith('.1.gz'):
                        pause()
                    return real_chmod(target, mode)

                with patch.object(logs.os, 'replace', replace), patch.object(logs.os, 'chmod', chmod):
                    rotation = threading.Thread(target=logs.rotate, args=(path, 1))
                    rotation.start()
                    self.assertTrue(reached.wait(3))
                    cleanup = subprocess.Popen([sys.executable, str(SCRIPT), td, '--clear-assistant'])
                    try:
                        time.sleep(.1)
                        self.assertIsNone(cleanup.poll(), 'cleanup crossed an active rotation boundary')
                    finally:
                        release.set()
                        rotation.join(5)
                        cleanup.wait(timeout=5)
                    self.assertEqual(cleanup.returncode, 0)
                path.write_text('SYNTHETIC_NEW_RUN')
                self.assertEqual(path.read_text(), 'SYNTHETIC_NEW_RUN')
                self.assertFalse(list(directory.glob('*.gz')))
                self.assertFalse(list(directory.glob('*.tmp')))

    def test_cleanup_removes_terminated_rotation_temps_and_only_owned_files(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            path = directory / 'assistant-agent.log'
            path.write_text('SYNTHETIC_TERMINATED_SENTINEL' * 100)
            ready = directory / 'ready'
            child = '\n'.join([
                'import importlib.util, pathlib, time',
                f's = importlib.util.spec_from_file_location("logs", {str(SCRIPT)!r})',
                'm = importlib.util.module_from_spec(s); s.loader.exec_module(m)',
                'def pause(src, dst):',
                f' pathlib.Path({str(ready)!r}).write_text(src)',
                ' while True: time.sleep(.02)',
                'm.os.replace = pause',
                f'm.rotate(pathlib.Path({str(path)!r}), 1)',
            ])
            process = subprocess.Popen([sys.executable, '-c', child])
            try:
                wait_for_path(ready)
                process.terminate()
                process.wait(timeout=3)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            self.assertTrue(list(directory.glob('*.tmp')))
            unrelated = directory / 'gateway.log.1.gz.owned.tmp'
            unrelated.write_text('SYNTHETIC_OTHER_SERVICE')
            logs.clear_logs(directory)
            self.assertEqual(path.read_bytes(), b'')
            self.assertEqual(list(directory.glob('*.tmp')), [unrelated])
            self.assertEqual(unrelated.read_text(), 'SYNTHETIC_OTHER_SERVICE')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertTrue((directory / '.log-maintainer.lock').exists())

    def test_sensitive_cleanup_stops_maintainer_first_and_propagates_failure(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'assistant-agent.log'
            path.write_text('SYNTHETIC_KEEP_UNTIL_STOPPED')
            result = run_bash(f'''
export ROOT={shlex.quote(str(ROOT))} LOG_DIR={shlex.quote(td)}
source {shlex.quote(str(STACK))}
stop_svc() {{ [[ "$1" == log-maintainer ]]; return 47; }}
clear_sensitive_assistant_logs
''', check=False)
            self.assertEqual(result.returncode, 47)
            self.assertEqual(path.read_text(), 'SYNTHETIC_KEEP_UNTIL_STOPPED')
            run_bash(f'''
export ROOT={shlex.quote(str(ROOT))} LOG_DIR={shlex.quote(td)}
source {shlex.quote(str(STACK))}
stop_svc() {{ [[ "$1" == log-maintainer ]]; }}
clear_sensitive_assistant_logs
''')
            self.assertEqual(path.read_bytes(), b'')


if __name__ == '__main__':
    unittest.main()
