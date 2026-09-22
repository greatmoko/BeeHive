import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wokbee.engine.lesson_summary import write_ai_lesson
from wokbee.engine.lesson_tools import build_experience_tools
from wokbee.engine.runner_experience_writer import ExperienceWriterMixin
from wokbee.engine.script_runner import load_pipeline


class LessonPipelineWriteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "scripts").mkdir()
        self.pipeline = self.root / "scripts" / "pipeline.json"
        self.old = '{"version":3,"steps":[{"type":"ai","description":"旧任务"}]}'
        self.pipeline.write_text(self.old, encoding="utf-8")
        self.script_files = [{"filename": "task.py", "content": "print('ok')"}]

    def write(self, **kwargs):
        return write_ai_lesson(
            self.root, project_id="test", goal="处理数据", summary="流程", **kwargs
        )

    def test_invalid_proposal_preserves_pipeline_and_does_not_save_lesson(self):
        for path in ("scripts/missing.py", ""):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "校验失败"):
                self.write(
                    script_files=self.script_files,
                    pipeline_steps=[{"type": "script", "path": path}],
                )
            self.assertEqual(self.pipeline.read_text(encoding="utf-8"), self.old)
            self.assertEqual(list((self.root / "memory" / "experiences").glob("*.md")), [])

    def test_invalid_first_proposal_does_not_create_pipeline(self):
        self.pipeline.unlink()
        with self.assertRaises(ValueError):
            self.write(pipeline_steps=[{"type": "script", "path": "scripts/missing.py"}])
        self.assertFalse(self.pipeline.exists())

    def test_valid_mixed_proposal_commits_once_and_syncs_lesson(self):
        from tokbee.core.safe_io import safe_write_text

        steps = [
            {"type": "ai", "description": "分析数据"},
            {"type": "script", "path": "scripts/task.py"},
        ]
        with patch("wokbee.engine.script_pipeline.safe_write_text", wraps=safe_write_text) as write:
            lesson = self.write(script_files=self.script_files, pipeline_steps=steps)
        pipeline_writes = [c for c in write.call_args_list if Path(c.args[0]) == self.pipeline]
        self.assertEqual(len(pipeline_writes), 1)
        actual = load_pipeline(self.root)["steps"]
        self.assertEqual([s["type"] for s in actual], ["ai", "script"])
        self.assertEqual(lesson.scripts, [actual[1]["path"]])
        self.assertTrue((self.root / lesson.scripts[0]).is_file())
        self.assertIn(lesson.scripts[0], lesson.success_path)
        self.assertEqual(steps[1]["path"], "scripts/task.py")

    def test_no_proposal_keeps_authored_script_and_excludes_helper(self):
        lesson = self.write(script_files=self.script_files + [
            {"filename": "helper.py", "content": "VALUE = 1", "in_pipeline": False},
        ])
        self.assertEqual(len(lesson.scripts), 1)
        self.assertEqual(lesson.scripts, [s["path"] for s in load_pipeline(self.root)["steps"]])

    def test_tool_failure_does_not_call_on_written(self):
        callback = Mock()
        tool = build_experience_tools(
            project_id="test", project_root=self.root, on_written=callback,
        )[0]
        result = tool.invoke({
            "summary": "流程",
            "pipeline_steps": [{"type": "script", "path": "scripts/missing.py"}],
        })
        self.assertTrue(result.startswith("错误："))
        callback.assert_not_called()
        self.assertEqual(self.pipeline.read_text(encoding="utf-8"), self.old)

    def test_summary_path_rejects_same_invalid_proposal(self):
        runner = ExperienceWriterMixin()
        runner.settings = SimpleNamespace(model_timeout_seconds=10)
        runner._phase_states = []
        runner._snapshot_run_events = lambda: []
        runner._emit = Mock()
        request = SimpleNamespace(
            project_root=self.root, project=SimpleNamespace(id="test", goal="处理数据"),
            user_message="处理数据",
            resolved=SimpleNamespace(api_key="test", api_host="test", provider_name="test", model_id="test"),
            approval=SimpleNamespace(summary=lambda: "test"),
        )
        prefix = "wokbee.engine.runner_experience_writer."
        with patch(prefix + "build_runtime_env_block", return_value=""), \
             patch(prefix + "build_chat_model"), \
             patch(prefix + "summarize_lesson_with_ai", return_value={
                 "script_files": self.script_files,
                 "pipeline_steps": [{"type": "script", "path": "scripts/missing.py"}],
             }), self.assertLogs("wokbee", level="ERROR"):
            result = runner._write_lesson(request, "success", "流程", "")
        self.assertIsNone(result)
        self.assertEqual(self.pipeline.read_text(encoding="utf-8"), self.old)
        self.assertEqual(list((self.root / "memory" / "experiences").glob("*.md")), [])


if __name__ == "__main__":
    unittest.main()
