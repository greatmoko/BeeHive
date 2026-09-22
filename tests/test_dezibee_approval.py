import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, PropertyMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication, QDialog, QTextEdit, QWidget
from dezibee.core.models import Requirement
from dezibee.core.services import DeziBeeWorker
from dezibee.ui.dezibee_view import DeziBeeView
from dezibee.ui.settings_workspace import DeziBeeSettingsWorkspace
from tokbee.ui.styles.theme import Theme
from wokbee.core.models import ApprovalFlags
from wokbee.core.settings import WokBeeSettings
from wokbee.engine.approval_policy import build_interrupt_on


class DeziBeeApprovalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.data = {}
        self.config = Mock()
        self.config.get.side_effect = self.data.get
        self.config.set.side_effect = self.data.__setitem__
        self.settings = WokBeeSettings(self.config)

    def test_settings_save_reload_and_worker_snapshot(self):
        req = Requirement(id="test")
        worker = DeziBeeWorker(req, "test", settings=self.settings)
        self.assertTrue(build_interrupt_on(worker._approval)["execute"])
        self.assertTrue(build_interrupt_on(worker._approval)["write_file"])
        with patch.object(Path, "mkdir"), patch.object(
            DeziBeeSettingsWorkspace, "_update_stat"
        ), patch("dezibee.ui.settings_workspace._tip"):
            self.settings.dezibee_work_root = Path(__file__).resolve().parents[1]
            page = DeziBeeSettingsWorkspace(Theme(), self.settings)
            self.assertFalse(page._approval_checks["skip_high_risk"].isChecked())
            page._approval_checks["skip_high_risk"].setChecked(True)
            page._on_save()
            reloaded = WokBeeSettings(self.config)
            self.assertTrue(reloaded.dezibee_approval.skip_high_risk)
            self.assertFalse(reloaded.approval.skip_high_risk)
            page._load()
            self.assertTrue(page._approval_checks["skip_high_risk"].isChecked())
            next_worker = DeziBeeWorker(req, "test", settings=reloaded)
            self.assertNotIn("execute", build_interrupt_on(next_worker._approval))
            self.assertIn("execute", build_interrupt_on(worker._approval))
            page.close()
        override = ApprovalFlags(skip_read=False)
        explicit = DeziBeeWorker(req, "test", settings=self.settings, approval=override)
        override.skip_high_risk = True
        self.assertIn("execute", build_interrupt_on(explicit._approval))
        self.assertIn("read_file", build_interrupt_on(explicit._approval))

    def test_worker_uses_design_entrypoint_with_configured_approval(self):
        worker = DeziBeeWorker(Requirement(id="test"), "设计", settings=self.settings)
        with patch.object(Requirement, "root", new_callable=PropertyMock, return_value=Mock()), patch(
            "dezibee.core.services.build_design_prompt", return_value="需求上下文"
        ), patch("dezibee.core.services.ProviderStore"), patch(
            "wokbee.engine.runner.resolve_model_for_project"
        ), patch("wokbee.engine.runner.AgentRunner") as runner:
            worker.run()
        runner.return_value.run_chat.assert_not_called()
        runner.return_value.run_design.assert_called_once()
        request = runner.return_value.run_design.call_args.args[0]
        self.assertEqual(request.runner_mode, "design")
        self.assertIn("execute", build_interrupt_on(request.approval))

    def test_dialog_routes_approval_and_rejection_to_originating_worker(self):
        class Receiver(QWidget):
            _on_approval_needed = DeziBeeView._on_approval_needed

        view = Receiver()
        view.theme = Theme()
        worker = DeziBeeWorker(Requirement(id="origin", title="原需求"), "test", settings=self.settings)
        worker._runner = Mock()
        other = Mock()
        view._workers = {"origin": worker, "other": other}
        view._sender_req_id = Mock(return_value="origin")
        items = [{"name": "execute", "args": {"command": "echo <test>"}}, {"name": "write_file"}]
        for result, decision in ((QDialog.DialogCode.Accepted, "approve"), (QDialog.DialogCode.Rejected, "reject")):
            def inspect_dialog(dlg):
                self.assertIn("origin", dlg.windowTitle())
                self.assertIn("echo <test>", dlg.findChild(QTextEdit).toPlainText())
                view._sender_req_id.return_value = "other"
                return result

            view._sender_req_id.return_value = "origin"
            with patch.object(worker, "isRunning", return_value=True), patch.object(QDialog, "exec", inspect_dialog):
                view._on_approval_needed(items)
            decisions = worker._runner.resolve_approval.call_args.args[0]
            self.assertEqual([d["type"] for d in decisions], [decision, decision])
            other.resolve_approval.assert_not_called()
        view.close()


if __name__ == "__main__":
    unittest.main()
