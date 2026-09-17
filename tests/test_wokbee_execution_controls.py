from __future__ import annotations

import sys
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wokbee.engine.lessons import merge_pipeline_steps
from wokbee.engine.runner import StepLimitExceeded, _StepBudget
from wokbee.engine.script_runner import group_phases
from wokbee.engine.tool_truncate import ToolConcurrencyLimiter
from wokbee.core.models import Project
from autobee.core.models import ScheduledTask, TaskType
from autobee.engine.executor import TaskExecutor


class WokBeeExecutionControlTests(unittest.TestCase):
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
            settings=SimpleNamespace(),
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
             patch("wokbee.engine.runner.RunRequest"), \
             patch("wokbee.engine.runner.AgentRunner") as runner_cls:
            runner_cls.return_value.run.return_value = run_result
            result = executor._run_wokbee(task)

        self.assertTrue(result["ok"])
        self.assertEqual([event.kind for event in project_store.events], ["user", "deliverables", "info"])
        self.assertEqual(project_store.events[1].content, "交付物目录")
        self.assertEqual(project_store.events[1].meta["source"], "autobee")
        self.assertEqual([args[1] for args in emitted], ["user", "deliverables", "info"])


if __name__ == "__main__":
    unittest.main()
