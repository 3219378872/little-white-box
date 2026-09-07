import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

from deploy.dev.tests.stack_support import (
    ROOT,
    STACK,
    run_bash,
    stop_test_process,
    unused_loopback_port,
    wait_for_port,
)


class StackPortCleanupTest(unittest.TestCase):
    @unittest.skipUnless(Path("/proc/self/environ").exists(), "requires procfs")
    def test_port_fallback_honors_owner_token_for_different_pid(self):
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
            recorded_token = "gateway:recorded:token:1"
            environment = os.environ.copy()
            environment["XBH_STACK_PROCESS_TOKEN"] = "gateway:other:token:2"
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
                pidfile.write_text("99999999\n", encoding="ascii")
                owner.write_text(f"{recorded_token}\n", encoding="ascii")
                script = f"""
export ROOT={shlex.quote(str(ROOT))}
export RUN_DIR={shlex.quote(str(run_dir))}
export LOG_DIR={shlex.quote(str(run_dir / 'logs'))}
export PID_DIR={shlex.quote(str(pid_dir))}
source {shlex.quote(str(STACK))}
listening_port_pids() {{ builtin printf '%s\n' {process.pid}; }}
port_open() {{ return 1; }}
stop_owned_port gateway 8888
"""
                result = run_bash(script, check=False)

                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(process.poll())
                self.assertIn("not managed gateway", result.stderr)
            finally:
                stop_test_process(process)

    def test_stopped_app_guard_rejects_an_untracked_gateway_port(self):
        script = f"""
export ROOT={shlex.quote(str(ROOT))}
source {shlex.quote(str(STACK))}
all_app_names() {{ printf 'gateway\n'; }}
validated_service_pid() {{ return 1; }}
service_process_pids() {{ return 0; }}
port_open() {{ return 0; }}
require_apps_stopped_for_patches
"""
        result = run_bash(script, check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("gateway-port", result.stderr)

    def test_stop_owned_port_fails_when_a_reported_owner_leaves_it_open(self):
        script = f"""
export ROOT={shlex.quote(str(ROOT))}
source {shlex.quote(str(STACK))}
listening_port_pids() {{ printf '4242\n'; }}
service_process_matches() {{ return 0; }}
stop_tree() {{ return 0; }}
port_open() {{ return 0; }}
stop_owned_port gateway 8888
"""
        result = run_bash(script, check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("remains occupied", result.stderr)

    def test_port_owner_listing_excludes_other_local_interfaces(self):
        script = f"""
export ROOT={shlex.quote(str(ROOT))}
source {shlex.quote(str(STACK))}
ss() {{
  printf '%s\n' \
    'LISTEN 0 128 127.0.0.2:8888 0.0.0.0:* users:(("x",pid=111,fd=3))' \
    'LISTEN 0 128 127.0.0.1:8888 0.0.0.0:* users:(("x",pid=222,fd=3))' \
    'LISTEN 0 128 0.0.0.0:8888 0.0.0.0:* users:(("x",pid=333,fd=3))' \
    'LISTEN 0 128 [::]:8888 [::]:* users:(("x",pid=444,fd=3))' \
    'LISTEN 0 128 [::1]:8888 [::]:* users:(("x",pid=555,fd=3))'
}}
listening_port_pids 8888
"""
        result = run_bash(script)

        self.assertEqual(result.stdout.splitlines(), ["222", "333", "444"])

    def test_app_down_leaves_unknown_port_owner_running(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            run_dir = temp / "run"
            port = unused_loopback_port()
            front_port = unused_loopback_port()
            while front_port == port:
                front_port = unused_loopback_port()
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "http.server",
                    str(port),
                    "--bind",
                    "127.0.0.1",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                wait_for_port(port)
                fake_bin = temp / "bin"
                fake_bin.mkdir()
                fuser = fake_bin / "fuser"
                fuser.write_text(
                    "#!/bin/sh\n"
                    "if [ \"$1\" = -k ] && "
                    "[ \"$2\" = \"$TEST_HTTP_PORT/tcp\" ]; then\n"
                    "  kill \"$TEST_HTTP_PID\"\n"
                    "elif [ \"$1\" = -n ] && [ \"$3\" = \"$TEST_HTTP_PORT\" ]; then\n"
                    "  printf '%s\\n' \"$TEST_HTTP_PID\"\n"
                    "fi\n",
                    encoding="utf-8",
                )
                fuser.chmod(0o700)
                script = f"""
export ROOT={shlex.quote(str(ROOT))}
export RUN_DIR={shlex.quote(str(run_dir))}
export LOG_DIR={shlex.quote(str(run_dir / 'logs'))}
export PID_DIR={shlex.quote(str(run_dir / 'pids'))}
export FRONT_PORT={front_port}
export GATEWAY_PORT={port}
export TEST_HTTP_PID={server.pid}
export TEST_HTTP_PORT={port}
export PATH={shlex.quote(str(fake_bin))}:$PATH
source {shlex.quote(str(STACK))}
proxy_down() {{ return 0; }}
app_down
"""
                result = run_bash(script, check=False)

                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(server.poll())
                self.assertIn(
                    f"leaving unknown process on :{port}", result.stderr
                )
                self.assertIn("not managed gateway", result.stderr)
            finally:
                stop_test_process(server)

    @unittest.skipUnless(
        Path("/proc/self/environ").exists() and shutil.which("perl"),
        "requires procfs and perl",
    )
    def test_app_down_preserves_initial_owner_fence_for_port_fallback(self):
        for service_name in ("gateway", "frontend"):
            with self.subTest(service_name=service_name):
                with tempfile.TemporaryDirectory() as tmp_dir:
                    temp = Path(tmp_dir)
                    run_dir = temp / "run"
                    bin_dir = run_dir / "bin"
                    pid_dir = run_dir / "pids"
                    bin_dir.mkdir(parents=True)
                    pid_dir.mkdir()
                    port = unused_loopback_port()
                    other_port = unused_loopback_port()
                    while other_port == port:
                        other_port = unused_loopback_port()
                    environment = os.environ.copy()
                    environment["XBH_STACK_PROCESS_TOKEN"] = (
                        f"{service_name}:actual:token:2"
                    )
                    if service_name == "gateway":
                        executable = bin_dir / "gateway"
                        shutil.copy2(shutil.which("perl"), executable)
                        executable.chmod(0o700)
                        process = subprocess.Popen(
                            [
                                str(executable),
                                "-MIO::Socket::INET",
                                "-e",
                                "my $s=IO::Socket::INET->new("
                                "LocalAddr=>'127.0.0.1',LocalPort=>$ARGV[0],"
                                "Listen=>16,ReuseAddr=>1) or die $!; "
                                "while (my $c=$s->accept()) { close $c }",
                                str(port),
                            ],
                            env=environment,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                    else:
                        bundle = temp / "bundle"
                        bundle.mkdir()
                        (bundle / "index.html").write_text(
                            "fixture", encoding="ascii"
                        )
                        process = subprocess.Popen(
                            [
                                sys.executable,
                                str(ROOT / "deploy" / "dev" / "serve_release.py"),
                                str(port),
                                str(bundle),
                            ],
                            env=environment,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                    try:
                        wait_for_port(port)
                        pidfile = pid_dir / f"{service_name}.pid"
                        owner = Path(f"{pidfile}.owner")
                        pidfile.write_text("99999999\n", encoding="ascii")
                        owner.write_text(
                            f"{service_name}:stale:token:1\n", encoding="ascii"
                        )
                        front_port = port if service_name == "frontend" else other_port
                        gateway_port = port if service_name == "gateway" else other_port
                        script = f"""
export ROOT={shlex.quote(str(ROOT))}
export RUN_DIR={shlex.quote(str(run_dir))}
export LOG_DIR={shlex.quote(str(run_dir / 'logs'))}
export PID_DIR={shlex.quote(str(pid_dir))}
export ETC_DIR={shlex.quote(str(temp / 'etc'))}
export APP_LIFECYCLE_LOCK={shlex.quote(str(temp / 'app.lock'))}
export FRONT_PORT={front_port}
export GATEWAY_PORT={gateway_port}
source {shlex.quote(str(STACK))}
proxy_down() {{ return 0; }}
set +e
app_down
first_status=$?
app_down
second_status=$?
set -e
builtin printf 'status=%s,%s\n' "$first_status" "$second_status"
"""
                        result = run_bash(script)

                        self.assertIn("status=1,1", result.stdout)
                        self.assertIsNone(process.poll())
                        self.assertEqual(
                            owner.read_text(encoding="ascii"),
                            f"{service_name}:stale:token:1\n",
                        )
                        self.assertIn(
                            f"not managed {service_name}", result.stderr
                        )
                    finally:
                        stop_test_process(process)

    @unittest.skipUnless(
        Path("/proc/self/environ").exists() and shutil.which("perl"),
        "requires procfs and perl",
    )
    def test_app_down_port_fallback_still_stops_legacy_ownerless_process(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            run_dir = temp / "run"
            bin_dir = run_dir / "bin"
            pid_dir = run_dir / "pids"
            bin_dir.mkdir(parents=True)
            pid_dir.mkdir()
            gateway = bin_dir / "gateway"
            shutil.copy2(shutil.which("perl"), gateway)
            gateway.chmod(0o700)
            port = unused_loopback_port()
            front_port = unused_loopback_port()
            while front_port == port:
                front_port = unused_loopback_port()
            process = subprocess.Popen(
                [
                    str(gateway),
                    "-MIO::Socket::INET",
                    "-e",
                    "my $s=IO::Socket::INET->new("
                    "LocalAddr=>'127.0.0.1',LocalPort=>$ARGV[0],"
                    "Listen=>16,ReuseAddr=>1) or die $!; "
                    "while (my $c=$s->accept()) { close $c }",
                    str(port),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                wait_for_port(port)
                script = f"""
export ROOT={shlex.quote(str(ROOT))}
export RUN_DIR={shlex.quote(str(run_dir))}
export LOG_DIR={shlex.quote(str(run_dir / 'logs'))}
export PID_DIR={shlex.quote(str(pid_dir))}
export ETC_DIR={shlex.quote(str(temp / 'etc'))}
export APP_LIFECYCLE_LOCK={shlex.quote(str(temp / 'app.lock'))}
export FRONT_PORT={front_port}
export GATEWAY_PORT={port}
source {shlex.quote(str(STACK))}
proxy_down() {{ return 0; }}
app_down
"""
                run_bash(script)

                process.wait(timeout=3)
            finally:
                stop_test_process(process)
