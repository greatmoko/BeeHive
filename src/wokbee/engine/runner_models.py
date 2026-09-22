"""运行请求、结果与步骤预算模型。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from tokbee.core.provider_store import ResolvedModel
from wokbee.core.models import ApprovalFlags, Project


@dataclass
class RunRequest:
    project: Project
    project_root: Path
    user_message: str
    resolved: ResolvedModel
    approval: ApprovalFlags
    max_steps: int = 128
    attachments: list[dict] = field(default_factory=list)
    runner_mode: str = ""
    chat_thread_id: str = ""
    design_context: str = ""


@dataclass
class RunResult:
    ok: bool
    outcome: str
    final_text: str = ""
    error: str = ""
    lesson_id: str = ""
    pending_actions: list[dict] = field(default_factory=list)


class StepLimitExceeded(RuntimeError):
    """单次 Agent 运行耗尽逻辑步骤预算。"""

    def __init__(self, limit: int, used: int, kind: str) -> None:
        self.limit = limit
        self.used = used
        self.kind = kind
        super().__init__(
            f"Agent 执行未完成：已达到 max_steps={limit} 的硬上限 "
            f"（已用 {used} 步，最后计数类型：{kind}）。"
            "已停止继续调用模型或工具，请提高 max_steps 或拆分任务。"
        )


class StepBudget:
    """线程安全的 Agent/工具逻辑步骤预算。"""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self.used = 0
        self._lock = threading.Lock()

    def consume(self, kind: str, amount: int = 1) -> None:
        amount = max(1, int(amount))
        with self._lock:
            if self.used + amount > self.limit:
                self.used = self.limit
                raise StepLimitExceeded(self.limit, self.used, kind)
            self.used += amount
