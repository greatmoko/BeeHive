import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import DEFAULT, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wokbee.core.models import ApprovalFlags
from wokbee.engine.runner_agent_assembly import AgentAssemblyMixin
from wokbee.engine.runner_assembly import prepare_project_root
from wokbee.engine.runner_modes import mode_policy


class RunnerModeTests(unittest.TestCase):
    def test_design_directory_preparation_skips_wokbee_layout(self):
        request = SimpleNamespace(project_root=Mock())
        with patch("wokbee.engine.runner_assembly.ensure_project_layout") as layout, patch(
            "wokbee.engine.runner_assembly.ensure_experience_files"
        ) as experience:
            # 使用真实 Path，mkdir 替换为 mock，避免写入用户目录。
            request.project_root = Path("design-test")
            with patch.object(Path, "mkdir") as mkdir:
                self.assertTrue(prepare_project_root(request, "design"))
                self.assertEqual(mkdir.call_count, 4)
            layout.assert_not_called()
            experience.assert_not_called()
        with self.assertRaises(ValueError):
            mode_policy("typo")

    def test_assembly_applies_mode_tools_experience_and_approval(self):
        names = (
            "prepare_project_root", "configure_design_write_validator", "build_chat_model",
            "ArchiveDeniedBackend", "CompositeBackend", "AccessCoerceBackend", "attach_execute_watch",
            "SkillsStore", "McpStore", "build_runtime_env_block", "build_project_meta_tools",
            "LessonStore", "peek_pipeline", "build_file_tools", "build_credential_tools", "build_experience_tools",
            "build_ask_user_tool", "build_access_request_tool", "build_autobee_tools",
            "FilesystemMiddleware", "wrap_read_file_soft_limit", "create_deep_agent", "_get_checkpointer",
        )
        runner = AgentAssemblyMixin()
        runner.settings = SimpleNamespace(
            model_timeout_seconds=180, tool_timeout_seconds=90, additional_directories=[],
            enable_deepseek_search=False, max_parallel_tools=4,
        )
        runner._cancel = threading.Event()
        runner._emit = Mock()
        runner.provider_store = Mock()
        runner._mark_experience_updated = Mock()
        runner._snapshot_run_events = Mock()
        req = SimpleNamespace(
            project=SimpleNamespace(id="req", title="需求", goal="设计"), project_root=Path("unused"),
            approval=ApprovalFlags(), resolved=SimpleNamespace(provider_name="test", model_id="test"),
            max_steps=64, user_message="设计",
        )
        for mode in ("design", "chat", "run"):
            with self.subTest(mode=mode), patch.multiple(
                "wokbee.engine.runner_agent_assembly", **dict.fromkeys(names, DEFAULT)
            ) as mocks, patch(
                "wokbee.engine.runner_agent_assembly.wrap_tools_runtime_controls", side_effect=lambda tools, **kw: tools
            ):
                mocks["SkillsStore"].return_value.list_enabled.return_value = []
                mocks["SkillsStore"].return_value.global_skills_paths.return_value = []
                mocks["SkillsStore"].return_value.skill_routes.return_value = []
                mocks["McpStore"].return_value.list_enabled.return_value = []
                mocks["build_runtime_env_block"].return_value = "运行环境"
                mocks["LessonStore"].return_value.prompt_digest.return_value = "经验"
                mocks["LessonStore"].return_value.is_empty.return_value = True
                for name in ("build_file_tools", "build_credential_tools", "build_autobee_tools", "build_project_meta_tools", "build_experience_tools"):
                    mocks[name].return_value = []
                runner.build_agent(req, mode=mode)
                kwargs = mocks["create_deep_agent"].call_args.kwargs
                self.assertTrue(kwargs["interrupt_on"]["execute"])
                self.assertTrue(kwargs["interrupt_on"]["write_file"])
                mocks["configure_design_write_validator"].assert_called_once_with(
                    mocks["ArchiveDeniedBackend"].return_value, req, mode == "design"
                )
                if mode == "design":
                    mocks["LessonStore"].assert_not_called()
                    mocks["build_experience_tools"].assert_not_called()
                    mocks["build_project_meta_tools"].assert_not_called()
                    self.assertNotIn("经验", runner._session_context_block)
                else:
                    mocks["LessonStore"].assert_called_once()
                    mocks["build_project_meta_tools"].assert_called_once()
                self.assertEqual(mocks["peek_pipeline"].call_count, int(mode == "run"))


if __name__ == "__main__":
    unittest.main()
