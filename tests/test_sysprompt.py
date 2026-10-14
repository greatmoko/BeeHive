from __future__ import annotations

import ast
import json
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import sysprompt
from tokbee.core.context_manager import build_summary_prompt_messages
from tokbee.core.session_settings import SessionSettings
from wokbee.engine.lesson_ai import summarize_lesson_with_ai


class SystemPromptTests(unittest.TestCase):
    def test_defaults_and_custom_prompt_roundtrip(self):
        self.assertEqual(SessionSettings().system_prompt, sysprompt.TOKBEE_SYSTEM_PROMPT)
        custom = SessionSettings(system_prompt="用户自定义角色")
        self.assertEqual(SessionSettings.from_dict(custom.to_dict()).system_prompt, custom.system_prompt)
        self.assertEqual(
            build_summary_prompt_messages([{"role": "user", "content": "hello"}])[0]["content"],
            sysprompt.SUMMARY_SYSTEM_PROMPT,
        )

    def test_design_context_does_not_repeat_static_rules(self):
        req = SimpleNamespace(title="后台管理", description="订单管理", root=Path("example"), device_shell="")
        before = sysprompt.static_system_prompt(mode="design")
        context = sysprompt.build_design_prompt(req)
        self.assertIn("默认设备外壳：browser", context)
        self.assertNotIn("WORKBENCH_DATA", context)
        self.assertNotIn("任何 PRD 修改前", context)
        for shell in ("phone", "tablet", "browser"):
            req.device_shell = shell
            self.assertIn(f"默认设备外壳：{shell}", sysprompt.build_design_prompt(req))
        req.device_shell = ""
        for title, shell in (("iPad应用", "tablet"), ("购物应用", "phone")):
            req.title, req.description = title, ""
            self.assertIn(f"默认设备外壳：{shell}", sysprompt.build_design_prompt(req))
        self.assertEqual(before, sysprompt.static_system_prompt(mode="design"))
        for mode in ("chat", "run", "design"):
            self.assertIn("全局记忆", sysprompt.static_system_prompt(mode=mode))
            self.assertNotIn("跨项目记忆库或对话记忆", sysprompt.static_system_prompt(mode=mode))

    def test_summary_request_uses_central_rules_and_keeps_facts(self):
        captured = []

        class Model:
            def stream(self, messages):
                captured.extend(messages)
                yield SimpleNamespace(content='{"summary":"摘要","success_path":"","notes":""}')

        result = summarize_lesson_with_ai(
            model=Model(), goal="目标", outcome="success", previous_experience="旧经验",
            run_log="真实日志", scripts_context="脚本列表", environment_hint="环境",
            phase_states="阶段状态",
        )
        self.assertEqual(result["summary"], "摘要")
        self.assertEqual(captured[0]["content"], sysprompt.EXPERIENCE_SUMMARY_SYSTEM_PROMPT)
        for fact in ("目标", "旧经验", "真实日志", "脚本列表", "环境", "阶段状态"):
            self.assertIn(fact, captured[1]["content"])
        self.assertNotIn("pipeline_steps", captured[1]["content"])
        prompt = captured[0]["content"]
        example = prompt[prompt.index('{\n'):prompt.index('\n}', prompt.index('{\n')) + 2]
        self.assertIn("success_path", json.loads(example))

    def test_autobee_model_receives_central_prompt(self):
        from autobee.engine.nl_builder import NLBuilder

        model = SimpleNamespace(api_host="local", api_key="", model_id="test", family="test", api_protocol="chat")
        with patch("autobee.engine.nl_builder.AIClient") as client:
            client.return_value.chat.return_value = SimpleNamespace(
                content='{"name":"任务","type":"text","schedule":"0 9 * * *","cron_text":"每天9点","config":{}}',
                reasoning_content="",
            )
            self.assertIsNotNone(NLBuilder().generate("每天9点提醒", model))
            messages = client.return_value.chat.call_args.args[0]
            self.assertEqual(messages[0]["content"], sysprompt.AUTOBEE_CONFIG_SYSTEM_PROMPT)

    def test_experience_tool_and_judge_share_update_policy(self):
        from wokbee.engine.lesson_tools import build_experience_tools

        tool = build_experience_tools(project_id="test", project_root=Path("."))[0]
        self.assertEqual(tool.description, sysprompt.EXPERIENCE_TOOL_DESCRIPTION)
        for prompt in (tool.description, sysprompt.EXPERIENCE_UPDATE_SYSTEM_PROMPT):
            self.assertIn("偶发网络错误、临时超时、仅结果变化或无实质新信息的取消不更新", prompt)
        self.assertIn("字段与更新条件见工具说明", sysprompt.WOKBEE_RUN_SYSTEM_PROMPT)

    def test_context_explicitly_labels_all_four_modes(self):
        from wokbee.engine.prompt import build_session_context_block
        for mode, first, expected in (
            ("chat", True, "wokbee/chat"), ("run", True, "wokbee/run/first"),
            ("run", False, "wokbee/run/repeat"), ("design", True, "dezibee/design"),
        ):
            with self.subTest(expected=expected):
                text = build_session_context_block(title="任务", goal="目标", approval_summary="审批", mode=mode, first_run=first)
                self.assertIn(f"【当前模式】{expected}", text)
                self.assertNotIn("记忆阶段", text)

    def test_mode_marker_reaches_text_and_attachment_turns_without_duplicating_context(self):
        from wokbee.engine.runner_flow import RunnerFlowMixin
        runner = RunnerFlowMixin()
        runner._cancel = threading.Event()
        runner._step_budget = None
        runner._prefix_guard = None
        runner._mode_label = "wokbee/run/repeat"
        marker = "【当前模式】wokbee/run/repeat"
        context = f"【会话上下文】\n{marker}\n目标"
        agent = Mock()
        agent.stream.return_value = iter(())
        for content in ("继续", context, [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}], [{"type": "text", "text": context}]):
            with self.subTest(content=content):
                payload = {"messages": [{"role": "user", "content": content}]}
                agent.stream.return_value = iter(())
                runner._stream_until_pause(agent, payload, {}, set())
                result = payload["messages"][0]["content"]
                text = result if isinstance(result, str) else result[0]["text"]
                self.assertEqual(text.count(marker), 1)
                if "会话上下文" in text:
                    self.assertTrue(text.startswith("【会话上下文】"))
                if isinstance(content, list) and content[0]["type"] == "image_url":
                    self.assertEqual(result[1], content[0])

    def test_no_inline_system_prompt_definitions(self):
        """防止新模型调用再次把固定 system 文本散落到业务模块。"""
        violations = []
        for path in SRC.rglob("*.py"):
            if path.name == "sysprompt.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                fields = {k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)}
                role = fields.get("role")
                content = fields.get("content")
                if isinstance(role, ast.Constant) and role.value == "system" and isinstance(content, (ast.Constant, ast.JoinedStr)):
                    violations.append(f"{path.relative_to(SRC)}:{node.lineno}")
        self.assertEqual(violations, [], "系统提示词应集中在 sysprompt.py")


if __name__ == "__main__":
    unittest.main()
