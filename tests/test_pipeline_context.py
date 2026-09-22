import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from wokbee.engine.runner_execution import AgentRunner
from wokbee.engine.runner_models import RunRequest
from wokbee.engine.script_runner import PipelineRunResult


class PipelineContextTests(unittest.TestCase):
    def test_ai_phase_output_is_not_repeated_in_next_phase_user_message(self):
        runner = AgentRunner.__new__(AgentRunner)
        runner.settings = SimpleNamespace(max_pipeline_phases=4)
        runner._event_log = Mock()
        runner._cancel = threading.Event()
        runner._emit = Mock()
        runner._ensure_lesson_written = Mock(return_value=None)
        state = {"messages": []}
        agent = Mock(get_state=Mock(side_effect=lambda _config: SimpleNamespace(values=state)))
        runner.build_agent = Mock(return_value=agent)
        user_messages = []

        def run_turn(*args, **kwargs):
            user_messages.append(kwargs["payload"]["messages"][0]["content"])
            state["messages"] = [
                {"role": "assistant", "content": f"AI output phase {len(user_messages)}"}
            ]
            return None

        runner._run_agent_turn = Mock(side_effect=run_turn)
        request = RunRequest(
            project=SimpleNamespace(id="test", goal="任务", title="项目"),
            project_root=Path("unused"),
            user_message="任务",
            max_steps=10,
            resolved=SimpleNamespace(provider_name="test", model_id="test"),
            approval=SimpleNamespace(summary=lambda: "test"),
        )

        def pipeline(_project_root, *, start_phase, prior_context, **_):
            context_parts = list(prior_context)
            if start_phase == 0:
                context_parts = ["脚本阶段输出"]
            return PipelineRunResult(
                ran=True,
                phases=[
                    {"type": "ai", "steps": [{}]},
                    {"type": "ai", "steps": [{}]},
                ],
                ok=True,
                need_ai=True,
                ai_steps=[{"description": f"阶段 {start_phase + 1}"}],
                next_phase_index=start_phase,
                reason=f"阶段 {start_phase + 1}",
                context_parts=context_parts,
                combined_output="\n\n".join(context_parts),
            )

        with patch("wokbee.engine.runner_flow._reset_run_state"), \
             patch("wokbee.engine.runner_flow.run_pipeline_until_ai_or_end", side_effect=pipeline):
            result = runner.run(request)

        self.assertTrue(result.ok)
        self.assertEqual(len(user_messages), 2)
        self.assertIn("脚本阶段输出", user_messages[0])
        self.assertNotIn("脚本阶段输出", user_messages[1])
        self.assertNotIn("AI output phase 1", user_messages[1])


if __name__ == "__main__":
    unittest.main()
