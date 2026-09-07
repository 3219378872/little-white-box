from pathlib import Path
import shlex
import tempfile
import unittest

from deploy.dev.tests.stack_support import (
    ROOT,
    STACK,
    run_bash,
)


class StackConfigTest(unittest.TestCase):
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
