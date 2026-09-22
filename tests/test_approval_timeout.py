import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from wokbee.core.project_run_queue import project_run_slot
from wokbee.core.settings import DEFAULTS
from wokbee.engine.runner_execution import AgentRunner


class ApprovalTimeoutTests(unittest.TestCase):
    def runner(self, timeout):
        runner = AgentRunner.__new__(AgentRunner)
        runner.settings = {"approval_timeout_seconds": timeout}
        runner._cancel = threading.Event()
        runner._approval_event = threading.Event()
        runner.on_approval_needed = None
        runner._emit = Mock()
        runner._stream_until_pause = Mock()
        return runner

    def test_timeout_rejects_and_releases_project_slot(self):
        runner = self.runner(0.01)
        pending = [{"name": "execute"}, {"name": "write_file"}]
        with patch("wokbee.engine.runner_flow._has_pending", return_value=True), \
             patch("wokbee.engine.runner_flow._first_ask_user_payload", return_value=None), \
             patch("wokbee.engine.runner_flow._pending_from_state", return_value=pending):
            with project_run_slot("approval-timeout-test"):
                result = runner._drain_pending_interrupts(
                    Mock(), {}, set(), SimpleNamespace(), allow_auto_lesson=False,
                )
        self.assertFalse(result.ok)
        self.assertEqual(result.outcome, "failed")
        decisions = runner._emit.call_args.args[2]["decisions"]
        self.assertEqual(decisions, [{"type": "reject", "message": "审批等待超时"}] * 2)
        runner._stream_until_pause.assert_not_called()
        acquired = threading.Event()

        def next_run():
            with project_run_slot("approval-timeout-test"):
                acquired.set()

        thread = threading.Thread(target=next_run, daemon=True)
        thread.start()
        thread.join(1)
        self.assertTrue(acquired.is_set())

    def test_response_and_cancel(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                runner = self.runner(10)

                def respond(pending):
                    runner._approval_decisions = [{"type": "approve"}]
                    if cancel:
                        runner._cancel.set()
                    runner._approval_event.set()

                runner.on_approval_needed = respond
                decisions = runner._wait_approval([{}])
                self.assertEqual(decisions[0]["type"], "reject" if cancel else "approve")
                self.assertFalse(runner._approval_timed_out)

    def test_default_and_invalid_config_have_finite_deadline(self):
        self.assertEqual(DEFAULTS["approval_timeout_seconds"], 43200)
        for value in (None, "bad", 0, -1, float("nan"), float("inf"), 43200):
            with self.subTest(value=value):
                runner = self.runner(value)
                with patch("wokbee.engine.runner_execution.time.monotonic", side_effect=[0, 43200]):
                    self.assertEqual(runner._wait_approval([{}])[0]["type"], "reject")
                self.assertTrue(runner._approval_timed_out)


if __name__ == "__main__":
    unittest.main()
