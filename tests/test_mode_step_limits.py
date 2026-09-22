import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wokbee.core.settings import WokBeeSettings
from wokbee.engine.runner_execution import AgentRunner
from wokbee.engine.runner_models import RunRequest, StepBudget, StepLimitExceeded
from wokbee.engine.script_runner import PipelineRunResult, PhaseResult, run_pipeline_until_ai_or_end


class ModeStepLimitTests(unittest.TestCase):
    def settings(self, initial=None):
        data = dict(initial or {})
        config = Mock()
        config.get.side_effect = lambda key, default=None: data.get(key, default)
        config.set.side_effect = lambda key, value: data.update({key: value})
        return WokBeeSettings(config), config

    def test_defaults_and_independent_saved_limits(self):
        settings, config = self.settings({"wokbee.max_steps": 40, "wokbee.max_pipeline_phases": 64})
        self.assertEqual((settings.run_max_steps, settings.chat_max_steps), (128, 64))
        settings.run_max_steps = 200
        settings.chat_max_steps = 20
        settings.save()
        reloaded = WokBeeSettings(config)
        self.assertEqual((reloaded.run_max_steps, reloaded.chat_max_steps), (200, 20))
        settings.run_max_steps = 0
        settings.chat_max_steps = 1000
        self.assertEqual((settings.run_max_steps, settings.chat_max_steps), (1, 500))

    def pipeline(self, count):
        return PipelineRunResult(ran=True, phases=[
            {"type": "script", "steps": [{"path": f"scripts/{i}.py"}]}
            for i in range(count)
        ])

    def test_scripts_and_agent_share_budget(self):
        budget = StepBudget(3)
        budget.consume("agent_turn")
        with patch("wokbee.engine.script_runner.peek_pipeline", return_value=self.pipeline(3)), \
             patch("wokbee.engine.script_runner.validate_pipeline_script_paths", return_value=[]), \
             patch("wokbee.engine.script_runner.run_script_phase", side_effect=lambda *a, **k: PhaseResult(type="script")) as execute:
            with self.assertRaises(StepLimitExceeded):
                run_pipeline_until_ai_or_end(Path("unused"), step_budget=budget)
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(budget.used, 3)

    def test_pipeline_can_finish_exactly_at_budget(self):
        with patch("wokbee.engine.script_runner.peek_pipeline", return_value=self.pipeline(2)), \
             patch("wokbee.engine.script_runner.validate_pipeline_script_paths", return_value=[]), \
             patch("wokbee.engine.script_runner.run_script_phase", side_effect=lambda *a, **k: PhaseResult(type="script")):
            result = run_pipeline_until_ai_or_end(Path("unused"), step_budget=StepBudget(2))
        self.assertTrue(result.ok)
        self.assertFalse(result.need_ai)

    def test_run_returns_incomplete_when_script_budget_exhausted(self):
        runner = AgentRunner.__new__(AgentRunner)
        runner._event_log = Mock()
        runner._cancel = threading.Event()
        runner._emit = Mock()
        runner.build_agent = Mock(return_value=Mock(get_state=Mock(return_value=SimpleNamespace(values={}))))
        runner._ensure_lesson_written = Mock(return_value=None)
        request = RunRequest(
            project=SimpleNamespace(id="test", goal="任务"), project_root=Path("unused"),
            user_message="任务", max_steps=2,
            resolved=SimpleNamespace(provider_name="test", model_id="test"),
            approval=SimpleNamespace(summary=lambda: "test"),
        )
        with patch("wokbee.engine.runner_flow._reset_run_state"), \
             patch("wokbee.engine.script_runner.peek_pipeline", return_value=self.pipeline(3)), \
             patch("wokbee.engine.script_runner.validate_pipeline_script_paths", return_value=[]), \
             patch("wokbee.engine.script_runner.run_script_phase", side_effect=lambda *a, **k: PhaseResult(type="script")), \
             self.assertLogs("wokbee", level="WARNING"):
            result = runner.run(request)
        self.assertFalse(result.ok)
        self.assertEqual(result.outcome, "incomplete")
        self.assertIn("max_steps=2", result.error)

    def test_run_returns_incomplete_when_pipeline_phase_limit_exhausted(self):
        runner = AgentRunner.__new__(AgentRunner)
        runner.settings = SimpleNamespace(max_pipeline_phases=1)
        runner._event_log = Mock()
        runner._cancel = threading.Event()
        runner._emit = Mock()
        runner.build_agent = Mock(
            return_value=Mock(get_state=Mock(return_value=SimpleNamespace(values={})))
        )
        runner._run_agent_turn = Mock(return_value=None)
        runner._ensure_lesson_written = Mock(return_value=None)
        request = RunRequest(
            project=SimpleNamespace(id="test", goal="任务"), project_root=Path("unused"),
            user_message="任务", max_steps=10,
            resolved=SimpleNamespace(provider_name="test", model_id="test"),
            approval=SimpleNamespace(summary=lambda: "test"),
        )

        def pipeline(_project_root, *, start_phase, **_):
            return PipelineRunResult(
                ran=True,
                phases=[{"type": "ai", "steps": [{}]}, {"type": "ai", "steps": [{}]}],
                need_ai=True,
                ai_steps=[{"description": "阶段任务"}],
                next_phase_index=start_phase,
            )

        with patch("wokbee.engine.runner_flow._reset_run_state"), \
             patch("wokbee.engine.runner_flow.run_pipeline_until_ai_or_end", side_effect=pipeline):
            result = runner.run(request)
        self.assertFalse(result.ok)
        self.assertEqual(result.outcome, "incomplete")
        self.assertIn("max_phases=1", result.error)

    def test_gateway_uses_chat_limit(self):
        from wokbee.gateway.dispatcher import GatewayDispatcher

        settings, _ = self.settings()
        dispatcher = GatewayDispatcher(settings, Mock(), Mock())
        dispatcher._wire_callbacks = Mock()
        with patch("wokbee.engine.ensure_engine_warm"), \
             patch("wokbee.engine.runner.resolve_model_for_project"), \
             patch("wokbee.engine.runner.AgentRunner") as runner:
            dispatcher.run_chat(SimpleNamespace(id="test"), "hello")
        request = runner.return_value.run_chat.call_args.args[0]
        self.assertEqual(request.max_steps, 64)

    def test_settings_page_loads_and_saves_both_limits(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QLabel
        from tokbee.ui.styles.theme import Theme
        from wokbee.ui.settings_workspace import WokBeeSettingsWorkspace

        app = QApplication.instance() or QApplication([])
        settings, _ = self.settings({"wokbee.workspace_root": str(Path.cwd())})
        prefix = "wokbee.ui.settings_workspace."
        with patch(prefix + "ProviderStore") as providers, \
             patch(prefix + "detect_terminal_apps", return_value=[]), \
             patch(prefix + "_tip"):
            providers.return_value.list_selectable_models.return_value = []
            page = WokBeeSettingsWorkspace(Theme(), settings)
            self.assertEqual((page._run_max_steps.value(), page._chat_max_steps.value()), (128, 64))
            self.assertNotIn("管线阶段上限", [label.text() for label in page.findChildren(QLabel)])
            page._run_max_steps.setValue(150)
            page._chat_max_steps.setValue(30)
            page._on_save()
            self.assertEqual((settings.run_max_steps, settings.chat_max_steps), (150, 30))
            page.close()
            page.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
