import os
from pathlib import Path
import re
import signal
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
STACK = ROOT / "deploy" / "dev" / "stack.sh"
JUSTFILE = ROOT / "justfile"


def run_bash(script, *, check=True, timeout=30):
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"bash failed with {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def stop_test_process(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)


def unused_loopback_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for_port(port, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.02)
    raise AssertionError(f"process did not listen on 127.0.0.1:{port}")


def wait_for_path(path, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"process did not create {path}")


def process_is_running(pid):
    try:
        stat_line = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return False
    state = stat_line.rsplit(") ", 1)[1].split(" ", 1)[0]
    return state not in {"X", "Z"}


def stop_test_pid_group(pid):
    for sig, timeout in ((signal.SIGTERM, 2), (signal.SIGKILL, 2)):
        if not process_is_running(pid):
            return
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + timeout
        while process_is_running(pid) and time.monotonic() < deadline:
            time.sleep(0.02)
    if process_is_running(pid):
        raise AssertionError(f"test process group {pid} did not stop")


class StackSecurityTest(unittest.TestCase):
    def test_override_ports_bind_only_to_loopback(self):
        override = (ROOT / "deploy" / "dev" / "middleware-override.yml").read_text()
        ports = re.findall(r'^\s*-\s*"([^"\n]+:[0-9]+)"\s*$', override, re.MULTILINE)
        self.assertTrue(ports)
        self.assertTrue(all(port.startswith("127.0.0.1:") for port in ports), ports)

    def test_database_secrets_are_not_docker_exec_argument_values(self):
        stack = STACK.read_text()
        dbprobe = (ROOT / "deploy" / "dev" / "e2e" / "dbprobe.py").read_text()
        self.assertNotIn('-e MYSQL_PWD="$pass"', stack)
        self.assertNotIn('-e REDISCLI_AUTH="$pass"', stack)
        self.assertNotIn('f"-p{MYSQL_PASSWORD}"', dbprobe)
        self.assertIn('env={"MYSQL_PWD": MYSQL_PASSWORD}', dbprobe)

    def test_grants_encode_passwords_and_enforce_role_boundaries(self):
        app_password = "A" * 32 + "'; DROP USER root; --"
        e2e_password = "B" * 32 + "' OR '1'='1"
        dsn_exports = "\n".join(
            f'export {key}="${{APP_MYSQL_USER}}:${{APP_MYSQL_PASSWORD}}@tcp(127.0.0.1:3306)/{schema}?parseTime=true"'
            for key, schema in (
                ("DB_CONTENT", "xbh_content"),
                ("DB_USER", "xbh_user"),
                ("DB_INTERACTION", "xbh_interaction"),
                ("DB_MEDIA", "xbh_media"),
                ("DB_MESSAGE", "xbh_message"),
                ("DB_FEED", "xbh_feed"),
                ("DB_ASSISTANT", "xbh_assistant"),
            )
        )
        script = f"""
source {shlex.quote(str(STACK))}
mysql_root() {{ cat; }}
export APP_MYSQL_USER=app_test
export APP_MYSQL_PASSWORD={shlex.quote(app_password)}
export E2E_MYSQL_USER=e2e_test
export E2E_MYSQL_PASSWORD={shlex.quote(e2e_password)}
{dsn_exports}
apply_dev_db_grants
"""
        output = run_bash(script).stdout
        self.assertNotIn(app_password, output)
        self.assertNotIn(e2e_password, output)
        self.assertNotIn("GRANT ALL PRIVILEGES ON", output)
        self.assertIn(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON xbh_assistant.* TO 'app_test'@'%';",
            output,
        )
        self.assertIn("GRANT SELECT ON xbh_assistant.* TO 'e2e_test'@'%';", output)
        self.assertIn("DROP USER IF EXISTS 'xbh'@'%';", output)

    def test_shared_or_legacy_accounts_are_rejected(self):
        script = f"""
source {shlex.quote(str(STACK))}
export APP_MYSQL_USER=xbh
export APP_MYSQL_PASSWORD={'a' * 48}
export E2E_MYSQL_USER=xbh
export E2E_MYSQL_PASSWORD={'b' * 48}
validate_dev_db_env
"""
        result = run_bash(script, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reserved legacy or root account", result.stderr)

    def test_rotation_preserves_non_db_settings_and_validates_new_dsns(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / "xbh-dev.env"
            env_path.write_text(
                "# retained header\n"
                "ASSISTANT_LLM_API_KEY='provider-test-value'\n"
                "DB_CONTENT=\"xbh:xbhdev@tcp(127.0.0.1:3306)/xbh_content?parseTime=true\"\n"
                "DB_USER=\"xbh:xbhdev@tcp(127.0.0.1:3306)/xbh_user?parseTime=true\"\n"
                "DB_INTERACTION=\"xbh:xbhdev@tcp(127.0.0.1:3306)/xbh_interaction?parseTime=true\"\n"
                "DB_MEDIA=\"xbh:xbhdev@tcp(127.0.0.1:3306)/xbh_media?parseTime=true\"\n"
                "DB_MESSAGE=\"xbh:xbhdev@tcp(127.0.0.1:3306)/xbh_message?parseTime=true\"\n"
                "DB_FEED=\"xbh:xbhdev@tcp(127.0.0.1:3306)/xbh_feed?parseTime=true\"\n"
            )
            os.chmod(env_path, 0o600)
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export ENV_FILE={shlex.quote(str(env_path))}
source {shlex.quote(str(STACK))}
rotate_dev_db_credentials
load_env
"""
            run_bash(script)
            rotated = env_path.read_text()
            self.assertIn("ASSISTANT_LLM_API_KEY='provider-test-value'", rotated)
            self.assertEqual(rotated.count("APP_MYSQL_PASSWORD="), 1)
            self.assertEqual(rotated.count("E2E_MYSQL_PASSWORD="), 1)
            self.assertIn(
                'DB_CONTENT="${APP_MYSQL_USER}:${APP_MYSQL_PASSWORD}@tcp(127.0.0.1:3306)/xbh_content?parseTime=true"',
                rotated,
            )
            app_password = re.search(r"^APP_MYSQL_PASSWORD=([0-9a-f]+)$", rotated, re.MULTILINE).group(1)
            e2e_password = re.search(r"^E2E_MYSQL_PASSWORD=([0-9a-f]+)$", rotated, re.MULTILINE).group(1)
            self.assertEqual(len(app_password), 48)
            self.assertEqual(len(e2e_password), 48)
            self.assertNotEqual(app_password, e2e_password)
            self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)

    def test_rotation_propagates_temp_write_failure_without_replacing_env(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            env_path = temp / "xbh-dev.env"
            original = (
                "# retained\n"
                "DB_CONTENT=xbh:xbhdev@tcp(127.0.0.1:3306)/xbh_content\n"
            )
            env_path.write_text(original, encoding="utf-8")
            os.chmod(env_path, 0o600)
            counter = temp / "secret-counter"
            run_dir = temp / "run"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export RUN_DIR={shlex.quote(str(run_dir))}
export LOG_DIR={shlex.quote(str(run_dir / 'logs'))}
export PID_DIR={shlex.quote(str(run_dir / 'pids'))}
export ETC_DIR={shlex.quote(str(temp / 'etc'))}
export ENV_FILE={shlex.quote(str(env_path))}
export APP_LIFECYCLE_LOCK={shlex.quote(str(temp / 'app.lock'))}
export TEST_SECRET_COUNTER={shlex.quote(str(counter))}
source {shlex.quote(str(STACK))}
random_hex_secret() {{
  if [[ -e "$TEST_SECRET_COUNTER" ]]; then
    builtin printf '%s' {'b' * 48}
  else
    : >"$TEST_SECRET_COUNTER"
    builtin printf '%s' {'a' * 48}
  fi
}}
printf() {{ return 61; }}
set +e
rotate_dev_db_credentials
status=$?
set -e
builtin printf 'status=%s\n' "$status"
"""
            result = run_bash(script)

            self.assertIn("status=61", result.stdout)
            self.assertEqual(env_path.read_text(encoding="utf-8"), original)
            self.assertEqual(list(temp.glob(".xbh-dev-env.*")), [])


class StackLifecycleTest(unittest.TestCase):
    def test_prepare_etc_binds_diagnostics_and_sets_agent_metrics_port(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            backend = temp / "backend"
            agent_config = (
                backend / "app" / "assistant" / "worker" / "etc" / "agent.yaml"
            )
            mq_config = backend / "app" / "search" / "mq" / "etc" / "search.yaml"
            agent_config.parent.mkdir(parents=True)
            mq_config.parent.mkdir(parents=True)
            agent_config.write_text(
                "Name: assistant-agent\n"
                "Prometheus:\n"
                "  Host: 0.0.0.0\n"
                "  Port: 9136\n",
                encoding="utf-8",
            )
            mq_config.write_text(
                "Name: search-mq\n"
                "Prometheus:\n"
                "  Host: 0.0.0.0\n"
                "  Port: 9133\n",
                encoding="utf-8",
            )
            etc_dir = temp / "etc"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export BACKEND={shlex.quote(str(backend))}
export ETC_DIR={shlex.quote(str(etc_dir))}
export ASSISTANT_AGENT_METRICS_PORT=01936
source {shlex.quote(str(STACK))}
prepare_etc
"""
            run_bash(script)

            rendered_agent = (
                etc_dir / "app" / "assistant" / "worker" / "etc" / "agent.yaml"
            ).read_text(encoding="utf-8")
            rendered_mq = (
                etc_dir / "app" / "search" / "mq" / "etc" / "search.yaml"
            ).read_text(encoding="utf-8")
            self.assertIn("  Host: 0.0.0.0", rendered_agent)
            self.assertIn("  Port: 1936", rendered_agent)
            self.assertIn("  Host: 0.0.0.0", rendered_mq)
            self.assertIn("  Port: 9133", rendered_mq)
            self.assertIn("  Host: 0.0.0.0", agent_config.read_text())

    def test_prepare_etc_rejects_invalid_agent_metrics_port(self):
        for port in ("0", "65536", "not-a-port"):
            with self.subTest(port=port), tempfile.TemporaryDirectory() as tmp_dir:
                script = f"""
export ROOT={shlex.quote(str(ROOT))}
export ETC_DIR={shlex.quote(str(Path(tmp_dir) / 'etc'))}
export ASSISTANT_AGENT_METRICS_PORT={shlex.quote(port)}
source {shlex.quote(str(STACK))}
prepare_etc
"""
                result = run_bash(script, check=False)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid assistant-agent metrics port", result.stderr)

    def test_prepare_etc_rejects_unrewritten_agent_metrics_config(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            backend = temp / "backend"
            agent_config = (
                backend / "app" / "assistant" / "worker" / "etc" / "agent.yaml"
            )
            agent_config.parent.mkdir(parents=True)
            agent_config.write_text(
                "Name: assistant-agent\n"
                "Prometheus:\n"
                "  Host: 0.0.0.0\n"
                "  Port: ${AGENT_METRICS_PORT}\n",
                encoding="utf-8",
            )
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export BACKEND={shlex.quote(str(backend))}
export ETC_DIR={shlex.quote(str(temp / 'etc'))}
source {shlex.quote(str(STACK))}
prepare_etc
"""
            result = run_bash(script, check=False)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "failed to configure assistant-agent Prometheus endpoint",
                result.stderr,
            )

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

    def test_app_up_success_does_not_roll_back(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            events = Path(tmp_dir) / "events"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export TEST_EVENTS={shlex.quote(str(events))}
export APP_LIFECYCLE_LOCK={shlex.quote(str(Path(tmp_dir) / 'app.lock'))}
source {shlex.quote(str(STACK))}
record() {{ printf '%s\\n' "$1" >>"$TEST_EVENTS"; }}
load_env() {{ record load_env; }}
ensure_assistant_db_env() {{ record ensure_assistant_db_env; }}
secure_runtime_paths() {{ record secure_runtime_paths; }}
clear_sensitive_assistant_logs() {{ record clear_sensitive_assistant_logs; }}
wipe_legacy_assistant_redis() {{ record wipe_legacy_assistant_redis; }}
prepare_etc() {{ record prepare_etc; }}
start_log_maintainer() {{
  record start_log_maintainer
  track_app_started_service log-maintainer
}}
start_row() {{
  name="${{1%%|*}}"
  record "start:$name"
  track_app_started_service "$name"
}}
wait_port() {{ record wait_port; }}
wait_http() {{ record wait_http; }}
wait_topics() {{ record wait_topics; }}
start_svc() {{ record start_svc; track_app_started_service "$1"; }}
frontend_up() {{ record frontend_up; track_app_started_service frontend; }}
proxy_up() {{ record proxy_up; track_app_started_service proxy; }}
maybe_rebuild_search() {{ record maybe_rebuild_search; }}
validate_all_app_processes() {{ record validate_all_app_processes; }}
http_code() {{ printf 200; }}
app_down() {{ record app_down; return 73; }}
stop_svc() {{ record "stop:$1"; }}
proxy_down() {{ record proxy_down; }}
app_up
"""
            result = run_bash(script)
            recorded = events.read_text(encoding="utf-8").splitlines()

            self.assertNotIn("app_down", recorded)
            self.assertIn("start:assistant-agent", recorded)
            self.assertIn("frontend_up", recorded)
            self.assertIn("proxy_up", recorded)
            self.assertIn("wait_http", recorded)
            self.assertIn("maybe_rebuild_search", recorded)
            self.assertIn("validate_all_app_processes", recorded)
            self.assertFalse(any(event.startswith("stop:") for event in recorded))
            self.assertNotIn("proxy_down", recorded)
            self.assertIn("entry http://127.0.0.1:", result.stdout)

    def test_app_up_rolls_back_only_new_starts_and_preserves_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            events = Path(tmp_dir) / "events"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export TEST_EVENTS={shlex.quote(str(events))}
export APP_LIFECYCLE_LOCK={shlex.quote(str(Path(tmp_dir) / 'app.lock'))}
source {shlex.quote(str(STACK))}
record() {{ printf '%s\\n' "$1" >>"$TEST_EVENTS"; }}
load_env() {{ record load_env; }}
ensure_assistant_db_env() {{ record ensure_assistant_db_env; }}
secure_runtime_paths() {{ record secure_runtime_paths; }}
clear_sensitive_assistant_logs() {{ record clear_sensitive_assistant_logs; }}
wipe_legacy_assistant_redis() {{ record wipe_legacy_assistant_redis; }}
prepare_etc() {{ record prepare_etc; }}
start_log_maintainer() {{ record start_log_maintainer; }}
start_row() {{
  name="${{1%%|*}}"
  record "start:$name"
  case "$name" in
    user-rpc) return 0 ;;
    content-rpc) track_app_started_service "$name" ;;
    media-rpc) return 42 ;;
  esac
}}
wait_port() {{ record wait_port; }}
wait_topics() {{ record wait_topics; }}
start_svc() {{ record start_svc; }}
frontend_up() {{ record frontend_up; }}
proxy_up() {{ record proxy_up; }}
maybe_rebuild_search() {{ record maybe_rebuild_search; }}
all_app_names() {{ printf '%s\n' gateway; }}
stop_svc() {{ record "stop:$1"; return 73; }}
proxy_down() {{ record proxy_down; return 74; }}
stop_owned_port() {{ record "stop_port:$1"; return 75; }}
set +e
app_up
status=$?
set -e
printf 'status=%s\\n' "$status"
"""
            result = run_bash(script)
            recorded = events.read_text(encoding="utf-8").splitlines()

            self.assertIn("status=42", result.stdout)
            self.assertIn("start:user-rpc", recorded)
            self.assertIn("start:content-rpc", recorded)
            self.assertIn("start:media-rpc", recorded)
            self.assertNotIn("start:interaction-rpc", recorded)
            self.assertNotIn("wait_port", recorded)
            self.assertNotIn("frontend_up", recorded)
            self.assertNotIn("proxy_up", recorded)
            self.assertEqual(recorded.count("stop:content-rpc"), 1)
            self.assertNotIn("stop:user-rpc", recorded)
            self.assertNotIn("stop:media-rpc", recorded)
            self.assertNotIn("proxy_down", recorded)
            self.assertNotIn("stop_port:frontend", recorded)
            self.assertNotIn("stop_port:gateway", recorded)
            self.assertIn("startup failed with status 42", result.stderr)
            self.assertIn("rollback also failed with status 73", result.stderr)

    def test_app_up_rolls_back_when_same_origin_entry_never_becomes_ready(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            events = temp / "events"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export TEST_EVENTS={shlex.quote(str(events))}
export APP_LIFECYCLE_LOCK={shlex.quote(str(temp / 'app.lock'))}
source {shlex.quote(str(STACK))}
record() {{ builtin printf '%s\n' "$1" >>"$TEST_EVENTS"; }}
load_env() {{ return 0; }}
ensure_assistant_db_env() {{ return 0; }}
secure_runtime_paths() {{ return 0; }}
clear_sensitive_assistant_logs() {{ return 0; }}
wipe_legacy_assistant_redis() {{ return 0; }}
prepare_etc() {{ return 0; }}
start_log_maintainer() {{ return 0; }}
start_row() {{ return 0; }}
wait_port() {{ return 0; }}
wait_topics() {{ return 0; }}
start_svc() {{ return 0; }}
frontend_up() {{ return 0; }}
proxy_up() {{ track_app_started_service proxy; }}
wait_http() {{ record wait_http; return 67; }}
maybe_rebuild_search() {{ record maybe_rebuild_search; }}
proxy_down() {{ record proxy_down; }}
set +e
app_up
status=$?
set -e
builtin printf 'status=%s\n' "$status"
"""
            result = run_bash(script)
            recorded = events.read_text(encoding="utf-8").splitlines()

            self.assertIn("status=67", result.stdout)
            self.assertEqual(recorded, ["wait_http", "proxy_down"])
            self.assertIn("startup failed with status 67", result.stderr)

    def test_app_up_final_sweep_detects_worker_that_exits_during_later_steps(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            events = temp / "events"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export TEST_EVENTS={shlex.quote(str(events))}
export APP_LIFECYCLE_LOCK={shlex.quote(str(temp / 'app.lock'))}
source {shlex.quote(str(STACK))}
agent_alive=0
load_env() {{ return 0; }}
ensure_assistant_db_env() {{ return 0; }}
secure_runtime_paths() {{ return 0; }}
clear_sensitive_assistant_logs() {{ return 0; }}
wipe_legacy_assistant_redis() {{ return 0; }}
prepare_etc() {{ return 0; }}
start_log_maintainer() {{ return 0; }}
start_row() {{
  name="${{1%%|*}}"
  if [[ "$name" == assistant-agent ]]; then
    agent_alive=1
    track_app_started_service assistant-agent
  fi
}}
wait_port() {{ return 0; }}
wait_topics() {{ return 0; }}
start_svc() {{ return 0; }}
frontend_up() {{ return 0; }}
proxy_up() {{ return 0; }}
wait_http() {{ return 0; }}
maybe_rebuild_search() {{ agent_alive=0; }}
all_app_names() {{ builtin printf '%s\n' assistant-agent; }}
validated_service_pid() {{ [[ "$agent_alive" == 1 ]]; }}
stop_svc() {{ builtin printf 'stop:%s\n' "$1" >>"$TEST_EVENTS"; }}
set +e
app_up
status=$?
set -e
builtin printf 'status=%s\n' "$status"
"""
            result = run_bash(script)

            self.assertIn("status=1", result.stdout)
            self.assertEqual(
                events.read_text(encoding="utf-8").splitlines(),
                ["stop:assistant-agent"],
            )
            self.assertIn(
                "assistant-agent is not running after application startup",
                result.stderr,
            )

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

    def test_middleware_up_stops_at_first_locked_callback_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            events = temp / "events"
            run_dir = temp / "run"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export RUN_DIR={shlex.quote(str(run_dir))}
export LOG_DIR={shlex.quote(str(run_dir / 'logs'))}
export PID_DIR={shlex.quote(str(run_dir / 'pids'))}
export ETC_DIR={shlex.quote(str(temp / 'etc'))}
export APP_LIFECYCLE_LOCK={shlex.quote(str(temp / 'app.lock'))}
export TEST_EVENTS={shlex.quote(str(events))}
source {shlex.quote(str(STACK))}
load_env() {{ printf '%s\n' load_env >>"$TEST_EVENTS"; return 42; }}
require_apps_stopped_for_patches() {{ printf '%s\n' guard >>"$TEST_EVENTS"; }}
require_compose_version() {{ printf '%s\n' compose-version >>"$TEST_EVENTS"; }}
compose() {{ printf '%s\n' compose >>"$TEST_EVENTS"; }}
set +e
middleware_up
status=$?
set -e
printf 'status=%s\n' "$status"
"""
            result = run_bash(script)

            self.assertIn("status=42", result.stdout)
            self.assertEqual(events.read_text(encoding="utf-8").splitlines(), ["load_env"])

    def test_middleware_down_refuses_to_stop_under_running_apps(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            events = temp / "events"
            run_dir = temp / "run"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export RUN_DIR={shlex.quote(str(run_dir))}
export LOG_DIR={shlex.quote(str(run_dir / 'logs'))}
export PID_DIR={shlex.quote(str(run_dir / 'pids'))}
export ETC_DIR={shlex.quote(str(temp / 'etc'))}
export APP_LIFECYCLE_LOCK={shlex.quote(str(temp / 'app.lock'))}
export TEST_EVENTS={shlex.quote(str(events))}
source {shlex.quote(str(STACK))}
require_apps_stopped_for_patches() {{
  printf 'guard:%s\n' "$1" >>"$TEST_EVENTS"
  return 55
}}
compose() {{ printf '%s\n' compose >>"$TEST_EVENTS"; }}
set +e
middleware_down
status=$?
set -e
printf 'status=%s\n' "$status"
"""
            result = run_bash(script)

            self.assertIn("status=55", result.stdout)
            self.assertEqual(
                events.read_text(encoding="utf-8").splitlines(),
                ["guard:middleware shutdown"],
            )

    def test_locked_patch_replay_stops_at_first_database_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            backend = temp / "backend"
            patch_dir = backend / "deploy" / "sql" / "patches"
            patch_dir.mkdir(parents=True)
            (patch_dir / "001-first.sql").write_text("SELECT 1;\n", encoding="ascii")
            (patch_dir / "002-second.sql").write_text("SELECT 2;\n", encoding="ascii")
            run_dir = temp / "run"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export BACKEND={shlex.quote(str(backend))}
export RUN_DIR={shlex.quote(str(run_dir))}
export LOG_DIR={shlex.quote(str(run_dir / 'logs'))}
export PID_DIR={shlex.quote(str(run_dir / 'pids'))}
export ETC_DIR={shlex.quote(str(temp / 'etc'))}
export APP_LIFECYCLE_LOCK={shlex.quote(str(temp / 'app.lock'))}
source {shlex.quote(str(STACK))}
calls=0
mysql_root() {{
  calls=$((calls + 1))
  [[ "$calls" -ne 1 ]] || return 37
}}
set +e
with_app_lifecycle_lock exclusive apply_sql_patches
status=$?
set -e
builtin printf 'status=%s calls=%s\n' "$status" "$calls"
"""
            result = run_bash(script)

            self.assertIn("status=37 calls=1", result.stdout)

    def test_locked_eval_seed_propagates_generator_pipeline_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            backend = temp / "backend"
            (backend / "eval").mkdir(parents=True)
            (backend / "scripts").mkdir()
            events = temp / "events"
            run_dir = temp / "run"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export BACKEND={shlex.quote(str(backend))}
export RUN_DIR={shlex.quote(str(run_dir))}
export LOG_DIR={shlex.quote(str(run_dir / 'logs'))}
export PID_DIR={shlex.quote(str(run_dir / 'pids'))}
export ETC_DIR={shlex.quote(str(temp / 'etc'))}
export APP_LIFECYCLE_LOCK={shlex.quote(str(temp / 'app.lock'))}
export TEST_EVENTS={shlex.quote(str(events))}
source {shlex.quote(str(STACK))}
python3() {{
  builtin printf '%s\n' python >>"$TEST_EVENTS"
  return 43
}}
mysql_root() {{
  builtin printf '%s\n' mysql >>"$TEST_EVENTS"
  cat >/dev/null
}}
set +e
with_app_lifecycle_lock exclusive apply_eval_corpus
status=$?
set -e
builtin printf 'status=%s\n' "$status"
"""
            result = run_bash(script)

            self.assertIn("status=43", result.stdout)
            recorded = events.read_text(encoding="utf-8").splitlines()
            self.assertCountEqual(recorded, ["python", "mysql"])

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

    def test_stack_up_serializes_concurrent_middleware_down(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            run_dir = temp / "run"
            entered_up = temp / "entered-up"
            contender_started = temp / "contender-started"
            entered_down = temp / "entered-down"
            release = temp / "release"
            events = temp / "events"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export RUN_DIR={shlex.quote(str(run_dir))}
export LOG_DIR={shlex.quote(str(run_dir / 'logs'))}
export PID_DIR={shlex.quote(str(run_dir / 'pids'))}
export ETC_DIR={shlex.quote(str(temp / 'etc'))}
export APP_LIFECYCLE_LOCK={shlex.quote(str(temp / 'app.lock'))}
export TEST_ENTERED_UP={shlex.quote(str(entered_up))}
export TEST_CONTENDER_STARTED={shlex.quote(str(contender_started))}
export TEST_ENTERED_DOWN={shlex.quote(str(entered_down))}
export TEST_RELEASE={shlex.quote(str(release))}
export TEST_EVENTS={shlex.quote(str(events))}
source {shlex.quote(str(STACK))}
up_pid=""
down_pid=""
cleanup() {{
  [[ -z "$up_pid" ]] || kill "$up_pid" 2>/dev/null || true
  [[ -z "$down_pid" ]] || kill "$down_pid" 2>/dev/null || true
}}
trap cleanup EXIT
app_down_locked() {{
  : >"$TEST_ENTERED_UP"
  builtin printf '%s\n' up-enter >>"$TEST_EVENTS"
  while [[ ! -e "$TEST_RELEASE" ]]; do
    command sleep 0.02
  done
  builtin printf '%s\n' up-release >>"$TEST_EVENTS"
}}
middleware_up_locked() {{ return 0; }}
app_up_locked() {{ return 0; }}
stack_status_locked() {{ return 0; }}
middleware_down_locked() {{
  : >"$TEST_ENTERED_DOWN"
  builtin printf '%s\n' down-enter >>"$TEST_EVENTS"
}}
stack_up &
up_pid=$!
for _ in {{1..100}}; do
  [[ -e "$TEST_ENTERED_UP" ]] && break
  command sleep 0.02
done
[[ -e "$TEST_ENTERED_UP" ]]
(
  : >"$TEST_CONTENDER_STARTED"
  middleware_down
) &
down_pid=$!
for _ in {{1..100}}; do
  [[ -e "$TEST_CONTENDER_STARTED" ]] && break
  command sleep 0.02
done
[[ -e "$TEST_CONTENDER_STARTED" ]]
command sleep 0.2
[[ ! -e "$TEST_ENTERED_DOWN" ]]
: >"$TEST_RELEASE"
wait "$up_pid"
up_pid=""
wait "$down_pid"
down_pid=""
trap - EXIT
"""
            run_bash(script, timeout=10)

            self.assertEqual(
                events.read_text(encoding="utf-8").splitlines(),
                ["up-enter", "up-release", "down-enter"],
            )

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
                "wait_port 127.0.0.1 9136", STACK.read_text(encoding="utf-8")
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

    def test_all_background_launchers_use_checked_pid_recording(self):
        stack = STACK.read_text(encoding="utf-8")

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

    def test_public_lifecycle_entrypoints_share_the_same_lock(self):
        stack = STACK.read_text(encoding="utf-8")
        justfile = JUSTFILE.read_text(encoding="utf-8")

        for public_name, locked_name in (
            ("rotate_dev_db_credentials", "rotate_dev_db_credentials_locked"),
            ("algorithm_up", "algorithm_up_locked"),
            ("algorithm_down", "algorithm_down_locked"),
            ("stack_up", "stack_up_locked"),
            ("stack_down", "stack_down_locked"),
            ("stack_restart", "stack_restart_locked"),
        ):
            with self.subTest(public_name=public_name):
                declaration = (
                    f"{public_name}() {{\n"
                    f"  with_app_lifecycle_lock exclusive {locked_name}\n"
                    "}"
                )
                self.assertIn(declaration, stack)

        for command in ("stack_up", "stack_down", "stack_restart"):
            self.assertEqual(justfile.count(f"    {command}\n"), 1)
        for nested_command in (
            "just app-down",
            "just app-up",
            "just middleware-down",
            "just middleware-up",
            "just down",
            "just up",
        ):
            self.assertNotIn(nested_command, justfile)

    def test_algorithm_down_propagates_compose_failure_through_lock(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp = Path(tmp_dir)
            run_dir = temp / "run"
            script = f"""
export ROOT={shlex.quote(str(ROOT))}
export RUN_DIR={shlex.quote(str(run_dir))}
export LOG_DIR={shlex.quote(str(run_dir / 'logs'))}
export PID_DIR={shlex.quote(str(run_dir / 'pids'))}
export ETC_DIR={shlex.quote(str(temp / 'etc'))}
export APP_LIFECYCLE_LOCK={shlex.quote(str(temp / 'app.lock'))}
source {shlex.quote(str(STACK))}
compose() {{ return 44; }}
set +e
algorithm_down
status=$?
set -e
builtin printf 'status=%s\n' "$status"
"""
            result = run_bash(script)

            self.assertIn("status=44", result.stdout)

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


if __name__ == "__main__":
    unittest.main()
