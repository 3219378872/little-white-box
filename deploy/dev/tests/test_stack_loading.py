from pathlib import Path
import shlex
import tempfile
import unittest

from deploy.dev.tests.stack_support import ROOT, STACK, run_bash, stack_source


class StackLoadingTest(unittest.TestCase):
    def test_source_resolves_modules_independently_of_cwd_and_business_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_bash(f"""
cd {shlex.quote(tmp)}
ROOT={shlex.quote(tmp + '/business')}
BACKEND={shlex.quote(tmp + '/custom-backend')}
source {shlex.quote(str(STACK))}
[[ "$ROOT" == {shlex.quote(tmp + '/business')} ]]
[[ "$BACKEND" == {shlex.quote(tmp + '/custom-backend')} ]]
[[ "${{RPC_SERVICES[0]}}" == *"$BACKEND"* ]]
declare -F app_up load_env validated_service_pid frontend_up e2e_agent_reset knowledge_check test_dev
""")
            self.assertIn("test_dev", result.stdout)

    def test_default_root_and_repeated_source_preserve_source_time_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_bash(f"""
cd {shlex.quote(tmp)}
unset ROOT BACKEND
source {shlex.quote(str(STACK))}
[[ "$ROOT" == {shlex.quote(str(ROOT))} ]]
[[ "$-" == *e* && "$-" == *u* ]]
[[ "$(set -o | awk '$1 == "pipefail" {{print $2}}')" == on ]]
[[ "$(umask)" == 0077 ]]
APP_UP_TRACK_STARTS=1
APP_UP_STARTED_SERVICES=(test)
source {shlex.quote(str(STACK))}
[[ "$APP_UP_TRACK_STARTS" == 0 && "${{#APP_UP_STARTED_SERVICES[@]}}" == 0 ]]
[[ "${{#RPC_SERVICES[@]}}" == 10 && "${{#MQ_SERVICES[@]}}" == 8 ]]
[[ ! -v _XBH_STACK_LIB_DIR ]]
""")

    def test_missing_module_is_not_hidden_by_conditional_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            shim = Path(tmp) / "stack.sh"
            shim.write_text(STACK.read_text(encoding="utf-8"), encoding="utf-8")
            result = run_bash(f"""
if source {shlex.quote(str(shim))}; then exit 99; else exit "$?"; fi
""", check=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn("lib/config.sh", result.stderr)

    def test_static_scan_follows_the_loaded_modules_not_unloaded_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lib").mkdir()
            shim = root / "stack.sh"
            shim.write_text('source "${_XBH_STACK_LIB_DIR}/config.sh" || return $?\n')
            (root / "lib/config.sh").write_text("loaded-security-marker\n")
            (root / "lib/unused.sh").write_text("unloaded-marker\n")
            text = stack_source(shim)
            self.assertIn("loaded-security-marker", text)
            self.assertNotIn("unloaded-marker", text)
