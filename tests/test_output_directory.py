"""交付目录切换到 output 的发布、兼容和归档回归检查。"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wokbee.core.paths import deliverables_dir, ensure_project_layout, list_deliverable_names
from wokbee.engine.script_pipeline import (
    _render_publish_script, events_touched_deliverables, goal_wants_deliverables,
)
from wokbee.engine.script_runner import publish_pipeline_outputs


class OutputDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_layout_preserves_legacy_files_and_lists_both_directories(self):
        legacy = self.root / "deliverables"
        legacy.mkdir()
        (legacy / "old.txt").write_text("历史成果", encoding="utf-8")
        ensure_project_layout(self.root)
        self.assertEqual(deliverables_dir(self.root), self.root / "output")
        (self.root / "output" / "new.txt").write_text("新成果", encoding="utf-8")
        self.assertCountEqual(list_deliverable_names(self.root), ["old.txt", "new.txt"])
        self.assertEqual((legacy / "old.txt").read_text(encoding="utf-8"), "历史成果")

    def test_both_publishers_write_output_and_preserve_binary_format(self):
        ensure_project_layout(self.root)
        source = self.root / "workspace" / "nested" / "report.bin"
        source.parent.mkdir()
        source.write_bytes(b"\x00\xff\x80")
        (self.root / "workspace" / "script_callback_test.md").write_text("中间回调", encoding="utf-8")
        target = self.root / "output" / "nested" / "report.bin"
        self.assertIn("nested/report.bin", publish_pipeline_outputs(self.root))
        self.assertEqual(target.read_bytes(), source.read_bytes())
        target.unlink()
        script = self.root / "scripts" / "publish.py"
        script.write_text(_render_publish_script(), encoding="utf-8")
        exec(compile(script.read_text(encoding="utf-8"), str(script), "exec"), {"__file__": str(script)})
        self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertFalse((self.root / "output" / "script_callback_test.md").exists())
        self.assertFalse((self.root / "deliverables").exists())

    def test_output_goal_and_tool_event_trigger_publication(self):
        self.assertTrue(goal_wants_deliverables("保存到 output/report.xlsx"))
        self.assertTrue(events_touched_deliverables([
            SimpleNamespace(meta={"args": {"file_path": "output/report.xlsx"}}),
        ]))
        self.assertFalse(goal_wants_deliverables("查询天气"))

    def test_archive_includes_output_and_legacy_files(self):
        from tokbee.core.config import Config
        from wokbee.core.settings import WokBeeSettings
        from wokbee.core.project_store import ProjectStore

        settings = WokBeeSettings(Config(str(self.root / "config.json")))
        settings.workspace_root = self.root / "projects"
        store = ProjectStore(settings)
        project = store.create("交付物归档测试")
        root = store.path_for(project.id)
        (root / "output" / "new.txt").write_text("新成果", encoding="utf-8")
        (root / "deliverables").mkdir()
        (root / "deliverables" / "old.txt").write_text("历史成果", encoding="utf-8")
        archive = store.archive_session(project.id)
        self.assertEqual((archive / "output" / "new.txt").read_text(encoding="utf-8"), "新成果")
        self.assertEqual((archive / "deliverables" / "old.txt").read_text(encoding="utf-8"), "历史成果")
        self.assertFalse((root / "output" / "new.txt").exists())


if __name__ == "__main__":
    unittest.main()
