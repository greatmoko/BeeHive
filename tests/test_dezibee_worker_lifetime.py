import sys
import time
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal
from dezibee.ui.dezibee_view import DeziBeeView


class WorkerLifetimeTests(unittest.TestCase):
    def test_result_arrives_before_thread_exit_without_releasing_worker(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        release = Event()

        class Worker(QThread):
            finished_result = Signal(object)

            def run(self):
                self.finished_result.emit(None)
                release.wait(3)

        class Receiver(QObject):
            _sender_req_id = DeziBeeView._sender_req_id
            _on_agent_finished = DeziBeeView._on_agent_finished
            _on_worker_stopped = DeziBeeView._on_worker_stopped

        receiver = Receiver()
        receiver.sidebar = Mock()
        receiver.sidebar.current_selected.return_value = "req"
        receiver.workspace = Mock()
        receiver._refresh = Mock()
        receiver._current_req = Mock(return_value=None)
        worker = Worker(receiver)
        receiver._workers = {"req": worker}
        worker.finished_result.connect(receiver._on_agent_finished)
        worker.finished.connect(receiver._on_worker_stopped)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        try:
            deadline = time.monotonic() + 2
            while not receiver._refresh.called and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
            self.assertTrue(receiver._refresh.called)
            self.assertIs(receiver._workers["req"], worker)
            self.assertTrue(worker.isRunning())
        finally:
            release.set()
            worker.wait(3000)
        deadline = time.monotonic() + 2
        while receiver._workers and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        self.assertEqual(receiver._workers, {})


if __name__ == "__main__":
    unittest.main()
