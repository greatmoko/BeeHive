import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tokbee.core.subprocess_util import CancellableRunResult
from wokbee.engine.archive_guard import ArchiveDeniedBackend, EXECUTE_OUTPUT_MAX_BYTES


class ExecuteOutputLimitTests(unittest.TestCase):
    @patch("wokbee.engine.archive_guard.run_cancellable")
    def test_execute_output_keeps_head_and_tail(self, run_cancellable):
        head = "HEAD-" + ("a" * 20_000)
        tail = "TAIL-" + ("z" * 20_000)
        run_cancellable.return_value = CancellableRunResult(
            stdout=head,
            stderr=tail,
            returncode=7,
        )
        backend = ArchiveDeniedBackend(root_dir=str(Path.cwd()))

        result = backend.execute("echo output")

        self.assertEqual(backend._max_output_bytes, EXECUTE_OUTPUT_MAX_BYTES)
        self.assertTrue(result.truncated)
        self.assertIn(head[:10_000], result.output)
        self.assertIn(tail[-5_000:], result.output)
        self.assertIn("Output truncated at 30000 bytes", result.output)
        self.assertIn("Exit code: 7", result.output)
        self.assertLess(len(result.output), 22_000)


if __name__ == "__main__":
    unittest.main()
