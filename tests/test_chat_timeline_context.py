import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wokbee.engine.runner_flow import RunnerFlowMixin
from wokbee.engine.runner_models import RunResult


class ChatTimelineContextTests(unittest.TestCase):
    def run_chat(self, values=None, *, state_error=False, chat_thread_id="", design_context="", entrypoint="run_chat"):
        runner = RunnerFlowMixin()
        runner._event_log = Mock()
        runner._configure_step_budget = Mock()
        runner._graph_config = lambda thread_id, req: {"configurable": {"thread_id": thread_id}}
        runner._emit = Mock()
        runner._recent_events_digest = Mock(return_value="旧时间线")
        agent = Mock()
        agent.get_state.return_value = SimpleNamespace(values=values)
        if state_error:
            agent.get_state.side_effect = RuntimeError("checkpoint unavailable")
        runner.build_agent = Mock(return_value=agent)
        runner._run_agent_turn = Mock(return_value=RunResult(ok=True, outcome="success"))
        request = SimpleNamespace(
            project=SimpleNamespace(id="project"), project_root=Path("unused"),
            user_message="本轮问题", attachments=[], chat_thread_id=chat_thread_id,
            resolved=SimpleNamespace(provider_name="test", model_id="test"),
            runner_mode="design" if design_context else "", design_context=design_context,
        )
        result = getattr(runner, entrypoint)(request)
        self.assertTrue(result.ok)
        call = runner._run_agent_turn.call_args
        agent.get_state.assert_called_once_with(call.args[1])
        content = call.kwargs["payload"]["messages"][0]["content"]
        self.assertIn("本轮问题", content)
        return runner, content, call.args[1]

    def test_empty_checkpoint_injects_timeline(self):
        for values in (None, {}, {"messages": []}):
            with self.subTest(values=values):
                runner, content, _ = self.run_chat(values)
                runner._recent_events_digest.assert_called_once_with(Path("unused"), limit=40)
                self.assertIn("旧时间线", content)

    def test_history_including_compacted_summary_skips_timeline(self):
        for message in (
            {"role": "user", "content": "之前的问题"},
            {"role": "system", "content": "压缩后的摘要"},
            SimpleNamespace(type="ai", content="之前的回复"),
        ):
            with self.subTest(message=message):
                runner, content, _ = self.run_chat({"messages": [message]})
                runner._recent_events_digest.assert_not_called()
                self.assertNotIn("近期时间线摘录", content)

    def test_uses_selected_chat_thread_checkpoint(self):
        _, _, config = self.run_chat({}, chat_thread_id="new-chat")
        self.assertEqual(config["configurable"]["thread_id"], "wokbee-chat-project-new-chat")

    def test_unreadable_checkpoint_does_not_assume_empty(self):
        with self.assertLogs("wokbee", level="ERROR"):
            runner, content, _ = self.run_chat(state_error=True)
        runner._recent_events_digest.assert_not_called()
        self.assertNotIn("近期时间线摘录", content)

    def test_design_context_only_on_first_turn_and_explicit_updates(self):
        from langchain_core.messages import convert_to_messages
        from langchain_openai.chat_models.base import _convert_message_to_dict

        context = "【需求上下文】\n当前需求：测试\n需求描述：旧描述\n工作目录：demo"
        runner, content, _ = self.run_chat({}, design_context=context)
        self.assertIn(context, content)
        history = runner._run_agent_turn.call_args.kwargs["payload"]["messages"]
        # 使用真实 LangChain 消息转换，验证版本随 checkpoint 保留但不发送给模型。
        history = convert_to_messages(history)
        self.assertIn("design_context", history[0].additional_kwargs)
        self.assertNotIn("design_context", _convert_message_to_dict(history[0]))
        runner, content, _ = self.run_chat({"messages": history}, design_context=context)
        self.assertNotIn("需求上下文", content)
        self.assertTrue(runner._context_injected)
        updated = context.replace("旧描述", "新描述")
        runner, content, _ = self.run_chat({"messages": history}, design_context=updated)
        self.assertIn("需求描述已更新为：新描述", content)
        self.assertIn("替代此前", content)
        self.assertNotIn("旧描述", content)
        self.assertTrue(any(c.args[0] == "user" for c in runner._emit.call_args_list))
        history = runner._run_agent_turn.call_args.kwargs["payload"]["messages"]
        _, content, _ = self.run_chat({"messages": history}, design_context=updated)
        self.assertNotIn("需求上下文", content)
        _, content, _ = self.run_chat({}, chat_thread_id="new", design_context=updated)
        self.assertIn(updated, content)

    def test_design_old_or_compacted_checkpoint_explicitly_syncs(self):
        context = "【需求上下文】\n需求描述：当前描述"
        for values in ({"messages": [{"role": "system", "content": "摘要"}]}, {}):
            runner, content, _ = self.run_chat(values, design_context=context)
            if values:
                self.assertIn("需求描述已更新为：当前描述", content)
            else:
                self.assertIn(context, content)

    def test_design_entrypoints_never_read_timeline(self):
        for entrypoint in ("run_design", "run_chat", "run"):
            for values in ({}, {"messages": [{"role": "system", "content": "摘要"}]}):
                with self.subTest(entrypoint=entrypoint, values=values):
                    runner, content, config = self.run_chat(
                        values, entrypoint=entrypoint, chat_thread_id="design-conv",
                        design_context="【需求上下文】\n需求描述：原型",
                    )
                    runner._recent_events_digest.assert_not_called()
                    self.assertNotIn("旧时间线", content)
                    runner.build_agent.assert_called_once()
                    self.assertEqual(runner.build_agent.call_args.kwargs["mode"], "design")
                    self.assertEqual(config["configurable"]["thread_id"], "wokbee-chat-project-design-conv")
                    self.assertFalse(runner._run_agent_turn.call_args.kwargs["allow_auto_lesson"])

    def test_design_without_context_still_skips_timeline(self):
        runner, content, _ = self.run_chat({}, entrypoint="run_design")
        runner._recent_events_digest.assert_not_called()
        self.assertNotIn("近期时间线摘录", content)
        self.assertIn("【记忆阶段】首次交互", content)
        self.assertNotIn("获取项目需求/目标", content)


if __name__ == "__main__":
    unittest.main()
