import gzip
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


_SPEC = importlib.util.spec_from_file_location(
    "log_maintainer", Path(__file__).with_name("log_maintainer.py")
)
assert _SPEC is not None and _SPEC.loader is not None
log_maintainer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(log_maintainer)


class LogMaintainerTest(unittest.TestCase):
    def test_maintain_rotates_large_logs_and_tightens_permissions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir) / "logs"
            log_dir.mkdir(mode=0o755)
            log_path = log_dir / "assistant-agent.log"
            content = b"private assistant output\n" * 100
            log_path.write_bytes(content)
            os.chmod(log_path, 0o664)

            self.assertEqual(log_maintainer.maintain(log_dir, 64), 1)

            backup = log_dir / "assistant-agent.log.1.gz"
            self.assertEqual(log_path.read_bytes(), b"")
            with gzip.open(backup, "rb") as stream:
                self.assertEqual(stream.read(), content)
            self.assertEqual(log_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual(log_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_maintain_leaves_small_log_in_place(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir)
            log_path = log_dir / "gateway.log"
            log_path.write_text("ok", encoding="utf-8")
            os.chmod(log_path, 0o664)

            self.assertEqual(log_maintainer.maintain(log_dir, 64), 0)
            self.assertEqual(log_path.read_text(encoding="utf-8"), "ok")
            self.assertEqual(log_path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
