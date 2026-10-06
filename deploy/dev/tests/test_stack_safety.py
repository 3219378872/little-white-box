import unittest

from deploy.dev.tests.stack_support import run_bash, source_stack


class AssistantMigrationSafetyTest(unittest.TestCase):
    def run_case(self, marker_table, marker, rows):
        script = f"""
{source_stack()}
mysql_root() {{
  if [[ "$*" == *"SELECT table_name"* ]]; then
    printf '%s\\n' assistant_message
  elif [[ "$*" == *"information_schema.tables"* ]]; then
    printf '%s\\n' {marker_table}
  elif [[ "$*" == *"runtime_marker WHERE"* ]]; then
    printf '%s\\n' {marker}
  else
    printf '%s\\n' {rows}
  fi
}}
require_safe_assistant_baseline
"""
        return run_bash(script, check=False)

    def test_existing_marker_preserves_populated_database(self):
        result = self.run_case(1, 1, 1)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_empty_database_can_initialize(self):
        for table in (0, 1):
            result = self.run_case(table, 0, 0)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_unmarked_user_data_cannot_be_reset(self):
        result = self.run_case(0, 0, 1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing legacy Assistant reset", result.stderr)


if __name__ == "__main__":
    unittest.main()
