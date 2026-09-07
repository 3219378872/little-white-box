from pathlib import Path
import re
import shlex
import tempfile
import unittest

from deploy.dev.tests.stack_support import (
    ROOT,
    STACK,
    JUSTFILE,
    run_bash,
    stack_source,
)


class StackLifecycleTest(unittest.TestCase):
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

    def test_public_lifecycle_entrypoints_share_the_same_lock(self):
        stack = stack_source()
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

    def test_stack_down_stops_algorithm_before_middleware(self):
        stack = stack_source()
        down_fn = re.search(
            r"^stack_down_locked\(\) \{\n(?:.*\n)*?^\}\n",
            stack,
            re.M,
        )
        self.assertIsNotNone(down_fn)
        self.assertIn("algorithm_down_locked", down_fn.group(0))
        self.assertLess(
            down_fn.group(0).index("algorithm_down_locked"),
            down_fn.group(0).index("middleware_down_locked"),
        )

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
app_down_locked() {{ echo app_down; }}
algorithm_down_locked() {{ echo algorithm_down; }}
middleware_down_locked() {{ echo middleware_down; }}
stack_down_locked
"""
            result = run_bash(script)

        self.assertEqual(
            [line for line in result.stdout.splitlines() if line],
            ["app_down", "algorithm_down", "middleware_down", "stopped"],
        )

    def test_stack_restart_does_not_stop_algorithm(self):
        stack = stack_source()
        restart_fn = re.search(
            r"^stack_restart_locked\(\) \{\n(?:.*\n)*?^\}\n",
            stack,
            re.M,
        )
        self.assertIsNotNone(restart_fn)
        self.assertNotIn("algorithm_down", restart_fn.group(0))
        self.assertNotIn("stack_down_locked", restart_fn.group(0))

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
app_down_locked() {{ echo app_down; }}
algorithm_down_locked() {{ echo algorithm_down; }}
middleware_down_locked() {{ echo middleware_down; }}
stack_up_locked() {{ echo stack_up; }}
stack_restart_locked
"""
            result = run_bash(script)

        self.assertEqual(
            [line for line in result.stdout.splitlines() if line],
            ["app_down", "middleware_down", "stack_up"],
        )
        self.assertNotIn("algorithm_down", result.stdout)
