from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wokbee.engine.archive_guard import ArchiveDeniedBackend
from wokbee.engine.access_coerce import AccessCoerceBackend
from wokbee.engine.file_tools import build_file_tools
from wokbee.core.paths import PROJECT_SUBDIRS, ensure_project_layout
from dezibee.core.preview import validate_workbench_document


class FileSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.backend = ArchiveDeniedBackend(root_dir=self.root, virtual_mode=True)
        self.tools = {t.name: t for t in build_file_tools(backend=AccessCoerceBackend(self.backend))}

    def test_project_layout_rejects_application_source_root(self):
        source_root = Path(__file__).resolve().parents[1]

        with self.assertRaisesRegex(ValueError, "应用源码目录"):
            ensure_project_layout(source_root)

    def test_project_layout_creates_standard_directories(self):
        root = self.root / "project"
        ensure_project_layout(root)

        self.assertTrue(all((root / name).is_dir() for name in PROJECT_SUBDIRS))

    def test_long_unicode_write_edit_find_read_delete(self):
        content = "中文🙂\n" * 20000 + "unique anchor\n"
        self.assertIsNone(self.backend.write("long.txt", content).error)
        found = self.tools["find_in_file"].invoke({"file_path": "long.txt", "query": "unique anchor"})
        self.assertIn("20001", found)
        self.assertIsNone(self.backend.edit("long.txt", "unique anchor", "changed").error)
        self.assertEqual((self.root / "long.txt").read_text(encoding="utf-8"), content.replace("unique anchor", "changed"))
        tail = self.tools["read_file_range"].invoke({"file_path": "long.txt", "offset": 20000})
        self.assertIn("changed", tail)
        self.assertIn("已到文件末尾", tail)
        self.assertIsNone(self.backend.delete("long.txt").error)
        self.assertFalse((self.root / "long.txt").exists())

    def test_failed_write_preserves_original_and_cleans_temporary(self):
        self.backend.write("a.txt", "original")
        self.assertIsNotNone(self.backend.write("a.txt", "bad\ud800").error)
        with patch("wokbee.engine.archive_guard.os.replace", side_effect=PermissionError("locked")):
            self.assertIsNotNone(self.backend.write("a.txt", "new").error)
            self.assertIsNotNone(self.backend.edit("a.txt", "original", "new").error)
        self.assertEqual((self.root / "a.txt").read_text(), "original")
        self.assertEqual(list(self.root.glob(".wokbee-*")), [])

    def test_chunks_stage_order_and_commit(self):
        self.backend.write("a.txt", "original")
        chunk = self.tools["write_file_chunk"]
        chunk.invoke(dict(file_path="a.txt", content="中文", mode="overwrite"))
        self.assertEqual((self.root / "a.txt").read_text(), "original")
        self.assertIn("错误", chunk.invoke(dict(file_path="a.txt", content="duplicate", offset=0)))
        result = chunk.invoke(dict(file_path="a.txt", content="🙂", offset=2, final=True))
        self.assertIn("已提交", result)
        self.assertEqual((self.root / "a.txt").read_text(encoding="utf-8"), "中文🙂")
        self.assertIn("错误", chunk.invoke(dict(file_path="a.txt", content="🙂", offset=2, final=True)))

    def test_long_single_line_has_explicit_character_cursor(self):
        self.backend.write("a.txt", "x" * 15000 + "THE_END")
        read = self.tools["read_file_range"]
        first = read.invoke(dict(file_path="a.txt"))
        self.assertIn("next_char_offset=10000", first)
        self.assertLess(len(first), 12000)
        last = read.invoke(dict(file_path="a.txt", char_offset=10000))
        self.assertIn("THE_END", last)
        self.assertIn("已到文件末尾", last)

    def test_insert_refuses_stale_snapshot(self):
        self.backend.write("a.txt", "unique anchor")
        original_edit = self.backend.edit

        def concurrent_edit(*args, **kwargs):
            self.backend.write("a.txt", "user changed content")
            return original_edit(*args, **kwargs)

        with patch.object(self.backend, "edit", side_effect=concurrent_edit):
            result = self.tools["insert_text"].invoke(dict(file_path="a.txt", anchor="unique anchor", text="extra"))
        self.assertIn("错误", result)
        self.assertEqual((self.root / "a.txt").read_text(), "user changed content")

    def test_guarded_paths_and_binary_are_not_rewritten(self):
        for path in ("archives/a.txt", "../outside.txt"):
            self.assertIsNotNone(self.backend.write(path, "bad").error)
        (self.root / "a.png").write_bytes(b"not text")
        result = self.tools["insert_text"].invoke(dict(file_path="a.png", anchor="text", text="oops"))
        self.assertIn("错误", result)
        self.assertEqual((self.root / "a.png").read_bytes(), b"not text")

    def test_workbench_validation_and_atomic_edit(self):
        template = Path(__file__).resolve().parents[1] / "src/dezibee/template/index.html"
        content = template.read_text(encoding="utf-8")
        validate_workbench_document(content)
        self.backend.write_validator = lambda path, text: validate_workbench_document(text)
        self.assertIsNone(self.backend.write("index.html", content).error)
        self.assertIsNotNone(self.backend.edit("index.html", "</html>", "").error)
        self.assertEqual((self.root / "index.html").read_text(encoding="utf-8"), content)
        with self.assertRaises(ValueError):
            validate_workbench_document('<html><body><script>const WORKBENCH_DATA = { pages: [</script></body></html>')


if __name__ == "__main__":
    unittest.main()
