import re
import subprocess
import unittest

from deploy.dev.tests.stack_support import (
    ROOT,
    JUSTFILE,
)


class StackEntrypointsTest(unittest.TestCase):
    def test_shebang_recipes_source_stack_via_root(self):
        justfile = JUSTFILE.read_text(encoding="utf-8")

        self.assertNotIn('cd "{{justfile_directory()}}"', justfile)
        self.assertNotIn("source deploy/dev/stack.sh", justfile)
        shebang_count = justfile.count("#!/usr/bin/env bash")
        self.assertGreater(shebang_count, 0)
        self.assertEqual(justfile.count('ROOT="{{root}}"'), shebang_count)
        self.assertEqual(justfile.count("# shellcheck source=/dev/null"), shebang_count)
        self.assertEqual(
            justfile.count('source "$ROOT/deploy/dev/stack.sh"'), shebang_count
        )

    def test_e2e_recipe_uses_positional_arguments(self):
        justfile = JUSTFILE.read_text(encoding="utf-8")

        self.assertNotIn("set -- {{args}}", justfile)
        self.assertNotIn('e2e *args="":', justfile)
        self.assertRegex(
            justfile,
            r"\[positional-arguments\]\n"
            r"e2e \*args:\n",
        )
        self.assertIn('exec python3 -m pytest -v "$@"', justfile)

    def test_justfile_list_documents_default_and_down_recipes(self):
        result = subprocess.run(
            ["just", "--list"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"just --list failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        for name in ("default", "middleware-down", "infer-down", "app-down"):
            with self.subTest(name=name):
                match = re.search(rf"^[ \t]+{re.escape(name)}\b(.*)$", result.stdout, re.M)
                self.assertIsNotNone(match, result.stdout)
                self.assertIn("#", match.group(1), match.group(0))
