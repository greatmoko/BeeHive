from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ui_common.attachments import AttachmentState, sanitize_attachment_name


class AttachmentStateTests(unittest.TestCase):
    def test_persists_clipboard_data_and_takes_items(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AttachmentState()
            state.set_uploads_root(Path(directory) / "uploads")
            item = {"kind": "file", "display_name": "note?.txt", "data": b"hello", "path": None}

            self.assertTrue(state.add(item))
            self.assertEqual(Path(item["path"]).read_bytes(), b"hello")
            self.assertEqual(item["display_name"], "note_.txt")
            self.assertEqual(state.take(), [item])
            self.assertEqual(state.items, [])

    def test_deduplicates_only_same_source_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.txt"
            source.write_text("source", encoding="utf-8")
            state = AttachmentState()
            first = {"path": source, "display_name": source.name}

            self.assertTrue(state.add(first))
            self.assertFalse(state.add({"path": source, "display_name": source.name}))
            self.assertEqual(sanitize_attachment_name("dir/name?.txt"), "dir_name_.txt")


if __name__ == "__main__":
    unittest.main()
