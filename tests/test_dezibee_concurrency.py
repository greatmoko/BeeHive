import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import Qt
from dezibee.core.models import Requirement
from dezibee.core.services import DeziBeeWorker
from wokbee.core.models import ApprovalFlags
from wokbee.core.project_run_queue import project_run_slot
from wokbee.engine.file_tools import build_file_tools


class DeziBeeConcurrencyTests(unittest.TestCase):
    def worker(self, req_id):
        return DeziBeeWorker(
            Requirement(id=req_id), "设计",
            settings=SimpleNamespace(dezibee_approval=ApprovalFlags(), chat_max_steps=64),
        )

    def test_same_requirement_queues_while_other_requirement_runs(self):
        first, same, other = (self.worker(req_id) for req_id in ("queue-a", "queue-a", "queue-b"))
        entered, release, same_started, other_started = (threading.Event() for _ in range(4))

        def hold_slot():
            entered.set()
            self.assertTrue(release.wait(5))

        first._run_in_slot = hold_slot
        same._run_in_slot = same_started.set
        other._run_in_slot = other_started.set
        with ThreadPoolExecutor(max_workers=3) as pool:
            a = pool.submit(first.run)
            try:
                self.assertTrue(entered.wait(2))
                b = pool.submit(same.run)
                c = pool.submit(other.run)
                self.assertTrue(other_started.wait(2))
                self.assertFalse(same_started.wait(0.1))
            finally:
                release.set()
            for future in (a, b, c):
                future.result(timeout=2)
        self.assertTrue(same_started.is_set())

    def test_cancelled_queued_worker_does_not_initialize_and_releases_slot(self):
        worker = self.worker("queue-cancel")
        results = []
        worker.finished_result.connect(results.append, Qt.ConnectionType.DirectConnection)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with project_run_slot(worker._req.id):
                future = pool.submit(worker.run)
                worker.cancel()
            with patch.object(Requirement, "root", new_callable=PropertyMock) as root:
                future.result(timeout=2)
                root.assert_not_called()
            self.assertEqual([r.outcome for r in results], ["cancelled"])
            next_worker = self.worker(worker._req.id)
            next_worker._run_in_slot = Mock()
            pool.submit(next_worker.run).result(timeout=2)
            next_worker._run_in_slot.assert_called_once()

    def test_slot_released_after_worker_exception(self):
        worker = self.worker("queue-error")
        worker._run_in_slot = Mock(side_effect=RuntimeError("failed"))
        with self.assertRaisesRegex(RuntimeError, "failed"):
            worker.run()
        worker._run_in_slot = Mock()
        worker.run()
        worker._run_in_slot.assert_called_once()

    def test_same_virtual_path_chunks_are_isolated_between_backends(self):
        # 同时暂存相同路径、相同 offset；若 pending 被改为全局，结果会串写。
        barrier = threading.Barrier(2)

        def write_chunks(label):
            backend = Mock()
            backend.write.return_value = SimpleNamespace(error=None)
            tool = next(t for t in build_file_tools(backend=backend) if t.name == "write_file_chunk")
            first = tool.invoke({"file_path": "demo/index.html", "content": label, "mode": "overwrite"})
            self.assertIn("next_offset=1", first)
            barrier.wait(timeout=3)
            final = tool.invoke({"file_path": "demo/index.html", "content": label, "offset": 1, "final": True})
            self.assertIn("已提交", final)
            backend.write.assert_called_once_with("demo/index.html", label * 2)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(write_chunks, label) for label in ("甲", "乙")]
            for future in futures:
                future.result(timeout=5)


if __name__ == "__main__":
    unittest.main()
