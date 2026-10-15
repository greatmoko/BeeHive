"""同一项目的运行串行化：所有入口共享 FIFO 队列。"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


@dataclass
class _ProjectGate:
    condition: threading.Condition
    next_ticket: int = 0
    serving: int = 0

    def acquire(self) -> None:
        with self.condition:
            ticket = self.next_ticket
            self.next_ticket += 1
            while ticket != self.serving:
                self.condition.wait()

    def release(self) -> None:
        with self.condition:
            self.serving += 1
            self.condition.notify_all()


_GATES: dict[str, _ProjectGate] = {}
_GATES_LOCK = threading.Lock()
# ponytail: gates live for process lifetime; add idle cleanup only if project-id churn matters.


def _gate_for(project_id: str) -> _ProjectGate:
    with _GATES_LOCK:
        gate = _GATES.get(project_id)
        if gate is None:
            gate = _ProjectGate(threading.Condition())
            _GATES[project_id] = gate
        return gate


@contextmanager
def project_run_slot(project_id: str) -> Iterator[None]:
    """按提交顺序占用项目运行槽；持有期间不打断当前运行。"""
    gate = _gate_for(str(project_id))
    gate.acquire()
    try:
        yield
    finally:
        gate.release()
