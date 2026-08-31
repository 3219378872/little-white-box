import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
STACK = ROOT / "deploy" / "dev" / "stack.sh"


def run_bash(script, *, check=True):
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"bash failed with {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


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


if __name__ == "__main__":
    unittest.main()
