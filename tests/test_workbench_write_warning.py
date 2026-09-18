import unittest

from wokbee.engine.archive_guard import _write_result_path


class WorkbenchWriteWarningTests(unittest.TestCase):
    def test_warning_keeps_write_result_successful(self):
        result = _write_result_path(
            "demo/index.html", "WORKBENCH_DATA 可能不完整"
        )
        self.assertIn("已写入", result)
        self.assertIn("WORKBENCH_DATA 可能不完整", result)

    def test_clean_write_result_keeps_path_unchanged(self):
        self.assertEqual(_write_result_path("demo/index.html", None), "demo/index.html")


if __name__ == "__main__":
    unittest.main()
