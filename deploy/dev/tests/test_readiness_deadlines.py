import importlib.util
from pathlib import Path
import shlex
import socket
import sys
import tempfile
import threading
import time
import unittest

from deploy.dev.tests.stack_support import ROOT, STACK, run_bash, unused_loopback_port

spec = importlib.util.spec_from_file_location('wait_ready', ROOT / 'deploy/dev/wait_ready.py')
ready = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ready)


class ReadinessDeadlinesTest(unittest.TestCase):
    def test_stalled_http_respects_total_budget_and_default_label(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            listener.settimeout(3)
            release = threading.Event()
            def stall():
                connection, _ = listener.accept()
                with connection:
                    release.wait(3)
            thread = threading.Thread(target=stall)
            thread.start()
            started = time.monotonic()
            try:
                result = run_bash(f'source {shlex.quote(str(STACK))}; wait_http http://127.0.0.1:{listener.getsockname()[1]}/ 0.3', check=False)
            finally:
                elapsed = time.monotonic() - started
                release.set()
                thread.join()
            self.assertEqual(result.returncode, 1)
            self.assertLess(elapsed, .8)
            self.assertIn('timeout waiting for http://', result.stderr)

    def test_refused_port_and_zero_budget_are_bounded(self):
        started = time.monotonic()
        result = run_bash(f'source {shlex.quote(str(STACK))}; wait_port 127.0.0.1 {unused_loopback_port()} .2', check=False)
        self.assertEqual(result.returncode, 1)
        self.assertLess(time.monotonic() - started, .7)
        self.assertIn('timeout waiting for 127.0.0.1:', result.stderr)
        self.assertFalse(ready.wait(lambda _: self.fail('zero budget must not probe'), 0))

    def test_slow_probe_is_killed_by_remaining_budget(self):
        started = time.monotonic()
        self.assertFalse(ready.run_probe([sys.executable, '-c', 'import time; time.sleep(5)'], .1))
        self.assertLess(time.monotonic() - started, .5)

    def test_budget_edge_and_sleep_use_monotonic_remaining_time(self):
        for duration, expected in ((.999, True), (1.001, False)):
            now = [0.0]
            def probe(remaining):
                self.assertEqual(remaining, 1)
                now[0] += duration
                return True
            self.assertEqual(ready.wait(probe, 1, clock=lambda: now[0], sleep=lambda _: self.fail()), expected)
        now = [0.0]
        sleeps = []
        def probe(_):
            now[0] += .8
            return False
        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds
        self.assertFalse(ready.wait(probe, 1, clock=lambda: now[0], sleep=sleep))
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], .2)

    def fake_docker(self, directory, statuses):
        state = Path(directory) / 'calls'
        docker = Path(directory) / 'docker'
        docker.write_text(
            '#!/bin/sh\n'
            f'[ "$1 $2 $3 $4" = "inspect -f {{{{.State.Health.Status}}}} xbh-mysql" ] || exit 9\n'
            f'n=$(cat {shlex.quote(str(state))} 2>/dev/null || echo 0)\n'
            f'echo $((n + 1)) > {shlex.quote(str(state))}\n'
            f'set -- {" ".join(statuses)}\n'
            'shift "$n" 2>/dev/null || true\n'
            'echo "${1:-starting}"\n',
            encoding='ascii',
        )
        docker.chmod(0o700)
        return state

    def test_container_healthy_waits_past_an_open_but_starting_container(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = self.fake_docker(directory, ['starting', 'starting', 'healthy'])
            result = run_bash(
                f'PATH={shlex.quote(directory)}:$PATH; source {shlex.quote(str(STACK))}; '
                'wait_healthy xbh-mysql 10 mysql')
            self.assertIn('ready: mysql', result.stdout)
            self.assertEqual(calls.read_text().strip(), '3')

    def test_container_healthy_times_out_while_unhealthy(self):
        with tempfile.TemporaryDirectory() as directory:
            self.fake_docker(directory, ['unhealthy'] * 20)
            started = time.monotonic()
            result = run_bash(
                f'PATH={shlex.quote(directory)}:$PATH; source {shlex.quote(str(STACK))}; '
                'wait_healthy xbh-mysql .3 mysql', check=False)
            self.assertEqual(result.returncode, 1)
            self.assertLess(time.monotonic() - started, .8)
            self.assertIn('timeout waiting for mysql', result.stderr)

    def test_invalid_budgets_rejected(self):
        for seconds in ('-1', 'nan', 'inf', 'not-a-number'):
            with self.subTest(seconds=seconds):
                result = run_bash(f'source {shlex.quote(str(STACK))}; wait_http http://127.0.0.1/ {seconds}', check=False)
                self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
