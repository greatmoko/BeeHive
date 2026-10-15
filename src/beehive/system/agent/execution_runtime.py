"""取消、审批/澄清等待和运行事件的通用控制状态。"""
from __future__ import annotations
import logging
import math
import threading
import time
from beehive.system.agent.execution_events import ExecutionEventLog

logger = logging.getLogger("wokbee")

class ExecutionRuntime:
    """一个执行对象独占中断状态，回调由业务提供。"""
    def __init__(self):
        self._cancel = threading.Event()
        self._approval_event = threading.Event()
        self._approval_decisions = None
        self._approval_timed_out = False
        self._ask_user_event = threading.Event()
        self._ask_user_answers = None
        self._ask_user_wait_timeout = 3600.0
        self._event_log = ExecutionEventLog()
        self.on_event = None
        self.on_approval_needed = None
        self.on_ask_user_needed = None

    def request_cancel(self) -> None:
        self._cancel.set()
        try:
            from beehive.common.process.subprocess_util import kill_all_cancellable_runs

            kill_all_cancellable_runs(cancel_event=self._cancel)
        except Exception:
            pass
        # 若卡在审批/澄清，解开等待
        self.resolve_approval([{"type": "reject", "message": "用户取消运行"}])
        self.resolve_ask_user({"cancelled": True})

    def resolve_approval(self, decisions: list[dict]) -> None:
        self._approval_decisions = decisions
        self._approval_event.set()

    def resolve_ask_user(self, answers: dict) -> None:
        self._ask_user_answers = answers if isinstance(answers, dict) else {"cancelled": True}
        self._ask_user_event.set()

    def _emit_stream_delta(self, target: str, delta: str) -> None:
        """流式增量事件（agent_stream）：只驱动 UI 实时气泡，不落盘、不入本轮轨迹。

        与 `_emit` 不同：`_emit` 会写事件缓冲（供对话/经验总结），token 级
        增量不能混进去，否则总结会读到半截文本；也不进 store，避免 events.jsonl 爆炸。
        """
        self._event_log.emit_stream(self.on_event, target, delta)

    def _emit(self, kind: str, content: str, meta: dict | None = None) -> None:
        self._event_log.emit(self.on_event, kind, content, meta)

    def _snapshot_run_events(self) -> list:
        """线程安全地取当前事件快照。"""
        return self._event_log.snapshot()

    def _wait_approval(self, pending: list[dict], *, timeout=43200) -> list[dict]:
        self._approval_event.clear()
        self._approval_decisions = None
        self._approval_timed_out = False
        try:
            timeout = float(timeout)
        except (TypeError, ValueError, OverflowError):
            timeout = 43200.0
        if not math.isfinite(timeout) or timeout <= 0:
            timeout = 43200.0
        deadline = time.monotonic() + timeout
        if self.on_approval_needed:
            self.on_approval_needed(pending)
        while True:
            if self._cancel.is_set():
                return [{"type": "reject", "message": "用户取消"} for _ in pending]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._approval_timed_out = True
                logger.warning("审批等待超时，自动拒绝")
                return [{"type": "reject", "message": "审批等待超时"} for _ in pending]
            if self._approval_event.wait(timeout=min(0.5, remaining)):
                break
        if self._cancel.is_set():
            return [{"type": "reject", "message": "用户取消"} for _ in pending]
        decisions = self._approval_decisions or [
            {"type": "reject", "message": "无审批结果"} for _ in pending
        ]
        # 数量对齐
        if len(decisions) < len(pending):
            decisions = list(decisions) + [
                {"type": "reject", "message": "未提供决策"}
                for _ in range(len(pending) - len(decisions))
            ]
        return decisions[: len(pending) or 1]

    def _wait_ask_user(self, payload: dict) -> dict:
        self._ask_user_event.clear()
        self._ask_user_answers = None
        if self.on_ask_user_needed:
            self.on_ask_user_needed(payload)
        deadline = time.monotonic() + (
            float(getattr(self, "_ask_user_wait_timeout", 3600.0) or 3600.0)
        )
        while not self._ask_user_event.wait(timeout=0.5):
            if self._cancel.is_set():
                return {"cancelled": True}
            if time.monotonic() >= deadline:
                logger.warning("等待澄清超时，按取消处理")
                return {"cancelled": True}
        return self._ask_user_answers or {"cancelled": True}
