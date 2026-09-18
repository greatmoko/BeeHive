import unittest

from wokbee.engine.file_tools import _replace_line_range


class EditFileLinesTests(unittest.TestCase):
    def test_replaces_inclusive_line_range(self):
        result = _replace_line_range("one\ntwo\nthree\nfour\n", 2, 3, "TWO\nTHREE")
        self.assertEqual(result, "one\nTWO\nTHREE\nfour\n")

    def test_deletes_line_range(self):
        self.assertEqual(_replace_line_range("one\ntwo\nthree\n", 2, 2, ""), "one\nthree\n")

    def test_inserts_at_end_of_file(self):
        self.assertEqual(_replace_line_range("one\ntwo\n", 3, 3, "three\n"), "one\ntwo\nthree\n")

    def test_rejects_invalid_range(self):
        with self.assertRaises(ValueError):
            _replace_line_range("one\n", 2, 1, "x")

    def test_schema_requires_line_range_and_rejects_old_string(self):
        from wokbee.engine.file_tools import build_file_tools

        tool = next(t for t in build_file_tools(backend=object()) if t.name == "edit_file_lines")
        schema = tool.args_schema.model_json_schema()
        self.assertEqual(
            set(schema["properties"]),
            {"file_path", "start_line", "end_line", "new_string"},
        )
        self.assertIn("start_line", schema["required"])
        self.assertIn("end_line", schema["required"])
        self.assertNotIn("old_string", schema["properties"])


if __name__ == "__main__":
    unittest.main()
