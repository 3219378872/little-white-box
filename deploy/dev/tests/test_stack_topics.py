from pathlib import Path
import tempfile
import unittest

from deploy.dev.tests.stack_support import (
    ROOT,
    run_bash,
    source_stack,
)


BOOTSTRAP = """#!/usr/bin/env bash
echo "[init] creating topics and consumer groups..."

TOPICS=(
  post-create post-update  # inline comment
  # retired-topic
  review-submitted
)

CONSUMER_GROUPS=(
  feed-service-group
)
"""


def write_backend(root, content):
    script = Path(root) / "deploy" / "rocketmq" / "init-topics.sh"
    script.parent.mkdir(parents=True)
    script.write_text(content, encoding="utf-8")
    return script


def topics_script(backend, body):
    return f"""
{source_stack(BACKEND=backend)}
{body}
"""


class RocketMQTopicsTest(unittest.TestCase):
    def test_bootstrap_topics_come_from_backend_init_script(self):
        with tempfile.TemporaryDirectory() as backend:
            write_backend(backend, BOOTSTRAP)

            result = run_bash(topics_script(backend, "rocketmq_bootstrap_topics"))

            self.assertEqual(
                result.stdout.splitlines(),
                ["post-create", "post-update", "review-submitted"],
            )

    def test_wait_topics_accepts_exactly_the_bootstrapped_topics(self):
        with tempfile.TemporaryDirectory() as backend:
            write_backend(backend, BOOTSTRAP)
            body = """
docker() { builtin printf '%s\\n' TBW102 post-create post-update review-submitted; }
sleep() { return 0; }
wait_topics 4
"""
            result = run_bash(topics_script(backend, body))

            self.assertIn("ready: rocketmq topics", result.stdout)

    def test_wait_topics_times_out_when_a_bootstrapped_topic_is_missing(self):
        with tempfile.TemporaryDirectory() as backend:
            write_backend(backend, BOOTSTRAP)
            body = """
docker() { builtin printf '%s\\n' post-create post-update; }
sleep() { return 0; }
wait_topics 4
"""
            result = run_bash(topics_script(backend, body), check=False)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("timeout waiting for rocketmq topics", result.stderr)

    def test_bootstrap_topics_fail_closed(self):
        cases = {
            "missing script": None,
            "missing list": "CONSUMER_GROUPS=(\n  a-group\n)\n",
            "empty list": "TOPICS=(\n)\n",
            "unterminated list": "TOPICS=(\n  post-create\n",
            "unsupported entry": 'TOPICS=(\n  "$EXTRA"\n)\n',
        }
        for name, content in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as backend:
                if content is not None:
                    write_backend(backend, content)
                body = """
docker() { echo "docker must not run" >&2; return 99; }
wait_topics 4
"""
                result = run_bash(topics_script(backend, body), check=False)

                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("docker must not run", result.stderr)

    def test_pinned_backend_bootstrap_script_parses(self):
        backend = ROOT / "little-white-box-content-community"
        if not (backend / "deploy" / "rocketmq" / "init-topics.sh").is_file():
            self.skipTest("backend submodule is not checked out")

        result = run_bash(topics_script(backend, "rocketmq_bootstrap_topics"))

        self.assertTrue(result.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
