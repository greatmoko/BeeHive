"""后台线程运行 AgentRunner，并通过信号回传 UI。"""

from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import QThread, Signal

# 说明：AgentRunner / RunRequest / RunResult 仅用于类型标注（postponed 到字符串），
# 不必在模块导入阶段加载 runner，避免启动即导入 deepagents 栈（由 start_engine_warmup 预热）。


class AgentWorker(QThread):
    event_emitted = Signal(str, str, object)  # kind, content, meta
    approval_needed = Signal(object)  # list[dict]
    ask_user_needed = Signal(object)  # dict payload
    finished_result = Signal(object)  # RunResult
    model_error = Signal(str)  # 模型解析失败（无可用模型等），主线程据此 _tip 并复位状态

    def __init__(
        self,
        settings,
        project,
        project_root,
        user_message,
        approval,
        max_steps,
        parent=None,
        *,
        mode: str = "run",
        attachments: list[dict] | None = None,
    ):
        super().__init__(parent)
        self._settings = settings
        self._project = project
        self._project_root = project_root
        self._user_message = user_message
        self._approval = approval
        self._max_steps = max_steps
        self.mode = mode  # run | chat
        self.attachments = list(attachments or [])
        self.request = None
        self._last_pending_count = 0  # 最近一次审批待决数量（由 _on_approval 填充）
        self._cancel_requested = threading.Event()

    def run(self):
        # 在 worker 线程内预热引擎并构造 runner/request：UI 线程绝不 import 重型引擎。
        from wokbee.engine import ensure_engine_warm

        ensure_engine_warm()
        from wokbee.engine.runner import (
            AgentRunner,
            RunRequest,
            RunResult,
            resolve_model_for_project,
        )

        try:
            resolved = resolve_model_for_project(self._project, self._settings)
        except Exception as e:
            self.model_error.emit(str(e))
            return

        if self._cancel_requested.is_set():
            self.finished_result.emit(
                RunResult(ok=False, outcome="cancelled", error="已取消")
            )
            return

        self.runner = AgentRunner(self._settings)
        self.request = RunRequest(
            project=self._project,
            project_root=self._project_root,
            user_message=self._user_message,
            resolved=resolved,
            approval=self._approval,
            max_steps=self._max_steps,
            attachments=self.attachments,
        )
        self.runner.on_event = self._on_event
        self.runner.on_approval_needed = self._on_approval
        self.runner.on_ask_user_needed = self._on_ask_user
        if self._cancel_requested.is_set():
            self.runner.request_cancel()

        try:
            if self.mode == "chat":
                result = self.runner.run_chat(self.request)
            else:
                result = self.runner.run(self.request)
        except Exception as e:
            # 意外异常兜底为 failed 结果，避免 UI 停在 RUNNING
            result = RunResult(ok=False, outcome="failed", error=str(e))
        self.finished_result.emit(result)

    def _on_event(self, kind: str, content: str, meta: dict):
        self.event_emitted.emit(kind, content, meta)

    def _on_approval(self, pending: list):
        self._last_pending_count = len(pending) if pending else 0
        self.approval_needed.emit(pending)

    def _on_ask_user(self, payload: dict):
        self.ask_user_needed.emit(payload)

    def cancel(self):
        self._cancel_requested.set()
        try:
            from tokbee.core.subprocess_util import kill_all_cancellable_runs

            if self.runner is not None:
                # 精确杀本次运行的进程树，不误杀其它并发运行的 execute
                kill_all_cancellable_runs(cancel_event=self.runner._cancel)
            else:
                kill_all_cancellable_runs()
        except Exception:
            pass
        if self.runner is not None:
            self.runner.request_cancel()

    def approve_all(self):
        if self.runner is None:
            return
        # 决策长度与最近一次审批待决数量对齐（避免写死 16，单轮 pending>16 时后面决策无人批）
        n = self._last_pending_count or 16
        self.runner.resolve_approval([{"type": "approve"}] * n)

    def reject_all(self, message: str = "用户拒绝"):
        if self.runner is None:
            return
        n = self._last_pending_count or 16
        self.runner.resolve_approval(
            [{"type": "reject", "message": message}] * n
        )

    def resolve_ask_user(self, answers: dict):
        if self.runner is not None:
            self.runner.resolve_ask_user(answers)


class LessonWorker(QThread):
    """后台总结经验，避免阻塞 UI 主线程。"""

    event_emitted = Signal(str, str, object)  # kind, content, meta
    finished_lesson = Signal(object)  # Lesson | None
    failed = Signal(str)

    def __init__(
        self,
        settings,
        project,
        project_root,
        *,
        user_message,
        outcome: str,
        summary: str,
        errors: str = "",
        success_path: str = "",
        notes: str = "",
        events: list[Any] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._settings = settings
        self._project = project
        self._project_root = project_root
        self._user_message = user_message
        self.outcome = outcome
        self.summary = summary
        self.errors = errors
        self.success_path = success_path
        self.notes = notes
        self.events = events
        self.runner = None  # 线程内才构造
        self.request = None

    def run(self):
        # 在 worker 线程内预热引擎并构造 runner/request：UI 线程绝不 import 重型引擎。
        from wokbee.engine import ensure_engine_warm

        ensure_engine_warm()
        from wokbee.engine.runner import (
            AgentRunner,
            RunRequest,
            resolve_model_for_project,
        )

        try:
            resolved = resolve_model_for_project(self._project, self._settings)
        except ValueError:
            # 无可用模型时仍尝试写经验（与旧 UI 行为一致：以空 resolved 降级走本地总结）
            from types import SimpleNamespace

            resolved = SimpleNamespace(
                provider_name="unknown",
                model_id="unknown",
                api_key="",
                api_host="",
            )
        except Exception as e:
            self.failed.emit(str(e))
            return

        self.runner = AgentRunner(self._settings)
        self.request = RunRequest(
            project=self._project,
            project_root=self._project_root,
            user_message=self._user_message,
            resolved=resolved,  # type: ignore[arg-type]
            approval=self._project.approval.copy(),
            max_steps=self._settings.max_steps,
        )
        self.runner.on_event = self._on_event
        try:
            lesson = self.runner.write_lesson_manual(
                self.request,
                self.outcome,
                self.summary,
                self.errors,
                success_path=self.success_path,
                notes=self.notes,
                events=self.events,
            )
            self.finished_lesson.emit(lesson)
        except Exception as e:
            self.failed.emit(str(e))

    def _on_event(self, kind: str, content: str, meta: dict):
        self.event_emitted.emit(kind, content, meta or {})

    def cancel(self):
        if self.runner is not None:
            self.runner.request_cancel()


class MemoryWorker(QThread):
    """后台更新跨项目 Agent 记忆（归档等场景），不阻塞 UI。

    线程内解析默认模型 → 读最新经验与对话记忆 → refresh_agent_memory 提炼 upsert。
    只更新记忆库条目；记忆概述由 update_memory_overview 工具按需触发。
    """

    event_emitted = Signal(str, str, object)  # kind, content, meta
    finished_memory = Signal()  # 成功或无事可做
    failed = Signal(str)

    def __init__(self, settings, project, project_root, *, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._project = project
        self._project_root = project_root
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()

    def run(self):
        from wokbee.engine import ensure_engine_warm

        ensure_engine_warm()
        from wokbee.engine.agent_memory import get_memory, refresh_agent_memory
        from wokbee.engine.chat_memory import chat_memory_path
        from wokbee.engine.model_factory import build_chat_model
        from wokbee.engine.runner import RunRequest, resolve_model_for_project
        from wokbee.engine.lessons import LessonStore

        try:
            if self._cancelled.is_set():
                self.finished_memory.emit()
                return
            try:
                resolved = resolve_model_for_project(self._project, self._settings)
            except Exception as e:
                self.failed.emit(str(e))
                return
            if not (
                getattr(resolved, "api_key", None)
                and getattr(resolved, "api_host", None)
            ):
                self.event_emitted.emit(
                    "info", "无可用模型密钥，跳过记忆库后台更新。", {}
                )
                self.finished_memory.emit()
                return
            if self._cancelled.is_set():
                self.finished_memory.emit()
                return

            # 无经验也无对话记忆时无事可做（避免空提炼覆盖既有条目）
            store = LessonStore(self._project_root)
            lesson_text = store.read_latest_text(max_chars=4000)
            chat_mem = ""
            try:
                cp = chat_memory_path(self._project_root)
                if cp.exists():
                    chat_mem = cp.read_text(encoding="utf-8")[-2000:]
            except OSError:
                chat_mem = ""
            if not (lesson_text or chat_mem).strip():
                self.event_emitted.emit(
                    "info", "无经验与对话记忆可提炼，跳过记忆库后台更新。", {}
                )
                self.finished_memory.emit()
                return

            self.request = RunRequest(
                project=self._project,
                project_root=self._project_root,
                user_message="",
                resolved=resolved,
                approval=self._project.approval.copy(),
                max_steps=self._settings.max_steps,
            )

            def _emit(kind: str, content: str, meta: dict | None = None) -> None:
                self.event_emitted.emit(kind, content, meta or {})

            _emit("info", "正在后台更新跨项目 Agent 记忆…")
            prev_row = get_memory(self._project.id, kind="agent")
            previous = str(prev_row.get("content") or "") if prev_row else ""
            chat = build_chat_model(
                resolved, timeout=self._settings.model_timeout_seconds
            )
            ok = refresh_agent_memory(
                model=chat,
                project_id=self._project.id,
                goal=self._project.goal or "",
                previous_agent_memory=previous,
                lesson_text=lesson_text,
                chat_memory_text=chat_mem,
                refs=[str(store.latest_path() or "")] if store.latest_path() else [],
                emit=_emit,
            )
            if not ok:
                _emit("info", "记忆库后台更新未写入（无新内容或提炼失败）。")
            self.finished_memory.emit()
        except Exception as e:
            self.failed.emit(str(e))
