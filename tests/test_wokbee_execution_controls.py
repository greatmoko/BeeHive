from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
import json
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
TEST_TMP_ROOT: Path

from wokbee.engine.lessons import (
    Lesson,
    build_success_path_from_pipeline,
    merge_pipeline_steps,
    render_lesson_md,
    sync_lesson_from_pipeline,
)
from wokbee.engine.script_factory import (
    _project_script_path_from_command,
    apply_ai_pipeline_steps,
    solidify_scripts,
)
from wokbee.engine.script_runner import (
    load_pipeline,
    run_pipeline_until_ai_or_end,
    resolve_pipeline_script_path,
    validate_pipeline_script_paths,
)
from wokbee.engine.runner import StepLimitExceeded, _StepBudget
from wokbee.engine.runtime_env import RuntimeEnv, build_runtime_env_block, build_runtime_env_settings_text, format_runtime_env_block
from wokbee.engine.archive_guard import ArchiveDeniedBackend
from wokbee.engine.script_runner import group_phases
from wokbee.engine.tool_truncate import ToolConcurrencyLimiter
from wokbee.core.models import Project
from autobee.core.models import ScheduledTask, TaskType
from autobee.engine.executor import TaskExecutor


class WokBeeExecutionControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        global TEST_TMP_ROOT
        TEST_TMP_ROOT = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_agent_context_does_not_confuse_app_runtime_dir_with_workdir(self):
        runtime = RuntimeEnv(
            os_name="Windows",
            cwd=r"C:\app-runtime",
            project_root=r"C:\agent-project",
            python_exe=r"C:\Python\python.exe",
        )
        block = format_runtime_env_block(runtime, for_agent=True)
        self.assertIn(r"Agent 工作目录（文件工具与项目文件的基准）：C:\agent-project", block)
        self.assertIn(r"execute 工作目录（脚本和相对路径命令的基准）：C:\agent-project", block)
        self.assertNotIn(r"C:\app-runtime", block)
        self.assertNotIn("当前工作目录", block)

    def test_settings_context_labels_app_runtime_dir_separately(self):
        runtime = RuntimeEnv(
            os_name="Windows",
            cwd=r"C:\app-runtime",
            project_root=r"C:\agent-project",
        )
        block = format_runtime_env_block(runtime)
        self.assertIn("应用运行目录（仅系统内部，不是 Agent 工作目录）", block)
        self.assertIn(r"C:\app-runtime", block)

    def test_settings_environment_text_uses_hierarchical_numbers(self):
        runtime = RuntimeEnv(os_name="Windows", python_exe=r"C:\Python\python.exe", probed_at="2026-09-23")
        with patch("wokbee.engine.runtime_env_format.get_runtime_env", return_value=runtime):
            text = build_runtime_env_settings_text()
        self.assertIn("1. 系统环境信息", text)
        self.assertIn("1.1. 最后探测", text)
        self.assertIn("1.2. 【运行环境】", text)
        self.assertIn("1.2.1. ", text)

    def test_agent_runtime_block_uses_project_root_not_cached_cwd(self):
        runtime = RuntimeEnv(
            os_name="Windows",
            cwd=r"C:\app-runtime",
            project_root=r"C:\agent-project",
            python_exe=r"C:\Python\python.exe",
        )
        with patch("wokbee.engine.runtime_env.collect_runtime_env", return_value=runtime):
            block = build_runtime_env_block(project_root=r"C:\agent-project")
        self.assertIn(r"Agent 工作目录（文件工具与项目文件的基准）：C:\agent-project", block)
        self.assertNotIn(r"C:\app-runtime", block)

    def test_execute_backend_cwd_is_agent_project_root(self):
        root = Path(TEST_TMP_ROOT).resolve()
        backend = ArchiveDeniedBackend(
            root_dir=str(root),
            virtual_mode=True,
            inherit_env=True,
        )
        self.assertEqual(Path(backend.cwd), root)

    def test_success_path_is_derived_from_pipeline_steps(self):
        steps = [
            {
                "type": "script",
                "path": "scripts/run.py",
                "tool": "script",
                "description": "运行用户脚本",
            },
            {
                "type": "script",
                "path": "scripts/publish.py",
                "tool": "publish",
                "description": "发布交付物",
            },
        ]
        path = build_success_path_from_pipeline(steps)
        self.assertEqual(len(path.splitlines()), len(steps))
        self.assertIn("scripts/run.py", path)
        self.assertIn("scripts/publish.py", path)
        self.assertIn('1. 脚本执行: "{脚本: scripts/run.py}"; 【运行用户脚本】', path)
        self.assertIn('2. 脚本执行: "{脚本: scripts/publish.py; 工具: publish}"; 【发布交付物】', path)
        self.assertNotIn("递归", path)

    def test_pipeline_rejects_missing_script_without_writing_ghost_path(self):
        root = TEST_TMP_ROOT
        with patch("wokbee.engine.script_factory.ensure_project_layout"), \
             patch("wokbee.engine.script_factory.safe_write_text") as write:
            result = apply_ai_pipeline_steps(
                root,
                lesson_id="lesson",
                pipeline_steps=[
                    {
                        "type": "script",
                        "path": "scripts/run_user_script.py",
                    }
                ],
            )
            self.assertFalse(result)
            write.assert_not_called()

    def test_pipeline_rejects_absolute_script_path(self):
        with patch("wokbee.engine.script_factory.ensure_project_layout"), \
             patch("wokbee.engine.script_factory.safe_write_text") as write:
            result = apply_ai_pipeline_steps(
                TEST_TMP_ROOT,
                lesson_id="lesson",
                pipeline_steps=[
                    {
                        "type": "script",
                        "path": r"C:\old-project\uploads\DataRefine2.py",
                    }
                ],
            )
            self.assertFalse(result)
            self.assertIsNone(
                resolve_pipeline_script_path(
                    TEST_TMP_ROOT,
                    r"C:\old-project\uploads\DataRefine2.py",
                )
            )
            write.assert_not_called()

    def test_pipeline_resolves_renamed_script(self):
        root = TEST_TMP_ROOT
        target = root / "scripts" / "make_release.ps1"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("Write-Output ok", encoding="utf-8")
        with patch("wokbee.engine.script_factory.ensure_project_layout"), \
             patch("wokbee.engine.script_factory.safe_write_text") as write:
            result = apply_ai_pipeline_steps(
                root,
                lesson_id="lesson",
                pipeline_steps=[
                    {
                        "type": "script",
                        "path": "scripts/run_user_script.py",
                    }
                ],
                rename_map={"run_user_script.py": "make_release.ps1"},
            )
            self.assertTrue(result)
            data = json.loads(write.call_args.args[1])
            self.assertEqual(data["steps"][0]["path"], "scripts/make_release.ps1")

    def test_uploaded_script_command_maps_to_current_uploads_path(self):
        old_command = (
            'cd "C:/old/project"; Copy-Item "uploads/DataRefine2.py" -Destination "."'
        )
        with patch("wokbee.engine.script_factory.Path.is_file", return_value=True):
            self.assertEqual(
                _project_script_path_from_command(TEST_TMP_ROOT, old_command, "uploads"),
                "uploads/DataRefine2.py",
            )
        with patch("wokbee.engine.script_runner.Path.is_file", return_value=True):
            self.assertEqual(
                validate_pipeline_script_paths(
                    TEST_TMP_ROOT,
                    [{"type": "script", "path": "uploads/DataRefine2.py"}],
                ),
                [],
            )

    def test_solidify_does_not_wrap_uploaded_script_copy_command(self):
        event = SimpleNamespace(
            kind="tool",
            content="",
            meta={
                "phase": "call",
                "tool": "execute",
                "args": {
                    "command": (
                        'cd "C:/old/project"; Copy-Item '
                        '"uploads/DataRefine2.py" -Destination "."'
                    )
                },
            },
        )
        with patch("wokbee.engine.script_factory.Path.is_file", return_value=True), \
             patch("wokbee.engine.script_factory.safe_write_text") as write:
            solidify_scripts(
                TEST_TMP_ROOT,
                lesson_id="lesson",
                goal="直接运行上传脚本并交付到 deliverables/",
                events=[event],
            )
            pipeline_writes = [
                call.args[1]
                for call in write.call_args_list
                if Path(call.args[0]).name == "pipeline.json"
            ]
            data = json.loads(pipeline_writes[-1])
            self.assertEqual(data["steps"][0]["path"], "uploads/DataRefine2.py")
            self.assertEqual(data["steps"][0]["tool"], "script")

    def test_pipeline_preflight_reports_missing_script_before_execution(self):
        root = TEST_TMP_ROOT
        data = {
            "steps": [
                {
                    "id": "script_1",
                    "type": "script",
                    "path": "scripts/run_user_script.py",
                }
            ]
        }
        with patch("wokbee.engine.script_runner.load_pipeline", return_value=data):
            self.assertEqual(
                validate_pipeline_script_paths(root),
                ["scripts/run_user_script.py"],
            )
            result = run_pipeline_until_ai_or_end(root)
            self.assertTrue(result.need_ai)
            self.assertEqual(len(result.phase_results), 1)
            self.assertIn("不存在", result.error_summary)
            self.assertIn("修复 pipeline", result.reason)

    def test_lesson_keeps_original_three_sections(self):
        lesson = Lesson(
            goal="运行脚本",
            summary="执行并发布",
            success_path="1. 脚本执行",
            errors="脚本路径缺失：改用实际固化路径",
        )
        rendered = render_lesson_md(lesson)
        self.assertIn("## 摘要（方法，非结果）", rendered)
        self.assertIn("## 成功实现路径", rendered)
        self.assertIn("## 注意事项", rendered)
        self.assertNotIn("## 实现过程", rendered)
        self.assertNotIn("压缩轨迹", rendered)
        self.assertNotIn("关键线索", rendered)
        self.assertNotIn("## 问题与解决方案", rendered)

    def test_sync_lesson_from_pipeline_aligns_scripts_and_path(self):
        root = TEST_TMP_ROOT
        data = {
            "steps": [
                {
                    "type": "script",
                    "path": "scripts/one.py",
                    "description": "执行一步",
                },
                {
                    "type": "ai",
                    "description": "整理业务内容",
                },
            ]
        }
        with patch("wokbee.engine.script_runner.load_pipeline", return_value=data):
            lesson = Lesson(goal="测试")
            self.assertTrue(sync_lesson_from_pipeline(lesson, root))
            self.assertEqual(lesson.scripts, ["scripts/one.py"])
            self.assertEqual(len(lesson.success_path.splitlines()), 2)

    def test_step_budget_is_hard_and_reports_limit(self):
        budget = _StepBudget(3)
        budget.consume("agent_turn")
        budget.consume("tool_update", 2)
        with self.assertRaises(StepLimitExceeded) as ctx:
            budget.consume("agent_turn")
        self.assertEqual(budget.used, 3)
        self.assertIn("max_steps=3", str(ctx.exception))
        self.assertIn("硬上限", str(ctx.exception))

    def test_pipeline_keeps_each_step_boundary(self):
        steps = [
            {"type": "script", "path": "scripts/one.py"},
            {"type": "script", "path": "scripts/two.py"},
            {"type": "ai", "description": "整理结果"},
            {"type": "ai", "description": "验证交付物"},
        ]
        phases = group_phases(steps)
        self.assertEqual(len(phases), 4)
        self.assertTrue(all(len(phase["steps"]) == 1 for phase in phases))
        self.assertEqual([phase["type"] for phase in phases], ["script", "script", "ai", "ai"])

    def test_tool_concurrency_limiter_caps_sync_bodies(self):
        limiter = ToolConcurrencyLimiter(2)
        lock = threading.Lock()
        active = 0
        peak = 0

        def worker() -> None:
            nonlocal active, peak
            limiter.acquire()
            try:
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.03)
                with lock:
                    active -= 1
            finally:
                limiter.release()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertLessEqual(peak, 2)

    def test_explicit_pipeline_order_is_not_prefixed_by_solid_scripts(self):
        solid = [SimpleNamespace(rel_path="scripts/collect.py", tool="execute", args={})]
        requested = [
            {"type": "ai", "description": "先整理现有输入"},
            {"type": "script", "path": "scripts/collect.py", "description": "再收集"},
        ]
        merged = merge_pipeline_steps(requested, solid, [])
        self.assertEqual([step["type"] for step in merged], ["ai", "script"])

    def test_autobee_success_adds_deliverables_event_before_finished(self):
        project = Project(id="p_autobee_test", goal="测试定时运行")

        class FakeProjectStore:
            def __init__(self):
                self.events = []
                self.statuses = []

            def get(self, project_id):
                return project if project_id == project.id else None

            def path_for(self, _project_id):
                return Path(".")

            def append_event(self, _project_id, event):
                self.events.append(event)

            def set_status(self, project_id, status, current_step="", **kwargs):
                self.statuses.append((project_id, status, current_step, kwargs))

        project_store = FakeProjectStore()
        emitted = []
        executor = TaskExecutor(
            store=SimpleNamespace(),
            project_store=project_store,
            provider_store=SimpleNamespace(),
            settings=SimpleNamespace(run_max_steps=128),
            event_sink=lambda *args: emitted.append(args),
        )
        executor._resolve_exec_model = lambda *_args: SimpleNamespace()
        task = ScheduledTask(
            id="ab_test",
            name="定时测试",
            task_type=TaskType.WOKBEE,
            project_id=project.id,
        )
        run_result = SimpleNamespace(
            ok=True,
            outcome="success",
            final_text="已完成",
            error="",
        )

        with patch("wokbee.engine.ensure_engine_warm"), \
             patch("wokbee.engine.runner.RunRequest") as request_cls, \
             patch("wokbee.engine.runner.AgentRunner") as runner_cls:
            runner_cls.return_value.run.return_value = run_result
            result = executor._run_wokbee(task)

        self.assertTrue(result["ok"])
        self.assertEqual(request_cls.call_args.kwargs["max_steps"], 128)
        self.assertEqual([event.kind for event in project_store.events], ["user", "deliverables", "info"])
        self.assertEqual(project_store.events[1].content, "交付物目录")
        self.assertEqual(project_store.events[1].meta["source"], "autobee")
        self.assertEqual([args[1] for args in emitted], ["user", "deliverables", "info"])


if __name__ == "__main__":
    unittest.main()
