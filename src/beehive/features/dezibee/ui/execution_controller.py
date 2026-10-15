"""按需求持有工作线程，接收事件并在退出后释放。"""
from __future__ import annotations
import logging
from beehive.features.wokbee.settings import WokBeeSettings
from beehive.features.dezibee.core.models import Requirement
from beehive.features.dezibee.core.services import DeziBeeWorker
from PySide6.QtCore import QObject
from beehive.features.dezibee.ui.execution_dialogs import on_approval_needed, on_ask_user_needed

logger = logging.getLogger("dezibee")

class DeziExecutionController(QObject):
    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self._workers: dict[str, DeziBeeWorker] = {}

    def _start_worker(self, req: Requirement, text: str, attachments: list | None = None):
        # 运行态只作用于当前查看的需求（其它需求可同时运行）
        self.view.workspace.input_bar.set_running(True)
        self.view.workspace.info_panel.set_running(True)
        # 通知网页端新一轮开始（重置工具行配对与流式气泡状态）
        self.view.workspace.chat_log.begin_run()

        worker = DeziBeeWorker(
            req,
            self._build_user_message(req, text),
            parent=self,
            attachments=attachments,
            conversation_id=req.active_conversation().conv_id,
            settings=WokBeeSettings(),
        )
        worker.finished.connect(self._on_worker_stopped)
        worker.finished.connect(worker.deleteLater)
        worker.event_emitted.connect(self._on_agent_event)
        worker.finished_result.connect(self._on_agent_finished)
        worker.ask_user_needed.connect(self._on_ask_user_needed)
        worker.approval_needed.connect(self._on_approval_needed)
        worker.model_error.connect(self._on_model_error)
        self._workers[req.id] = worker
        # 列表项立刻亮起「运行中」标识
        self.view.sidebar.set_running_ids(set(self._workers.keys()))
        worker.start()

    def _build_user_message(self, req: Requirement, text: str) -> str:
        return text

    def _on_agent_event(self, kind: str, content: str, meta: dict):
        # 事件归属发出它的 worker 对应的需求，与当前左侧选中的需求无关
        req_id = self._sender_req_id()
        if req_id is None:
            return
        req = self.view._store.get(req_id)
        if req is None:
            return
        conv = req.active_conversation()
        meta = meta if isinstance(meta, dict) else {}
        target = "reasoning" if str(meta.get("target") or "") == "reasoning" else "text"
        if kind == "agent_stream":
            # 流式增量：只驱动网页实时气泡，不落盘（完整 agent 事件到达时前端自动定稿去重）
            if self.view.sidebar.current_selected() == req_id:
                self.view.workspace.chat_log.append_stream(target, content)
            return
        # 记录到归属需求的对话（agent/user/error/info/tool），工具事件保留结构化 meta
        if kind in ("agent", "user", "error", "info", "tool", "approval"):
            self.view._append_conv_event(conv, kind, content, meta)
            # 只有正在查看该需求时才渲染，避免对话串台
            if self.view.sidebar.current_selected() == req_id:
                self.view.workspace.chat_log._render_event(
                    {"kind": kind, "content": content, "meta": meta}
                )
            self.view._persist(req)

    def _sender_req_id(self) -> str | None:
        """当前信号发送者（worker）归属的需求 ID。"""
        sender = self.sender()
        for rid, w in self._workers.items():
            if w is sender:
                return rid
        return None

    def _on_worker_stopped(self):
        # 结果信号可能先于线程真正退出；只在 finished 中释放引用。
        req_id = self._sender_req_id()
        if req_id is not None:
            self._workers.pop(req_id, None)
        self.view.sidebar.set_running_ids(set(self._workers))
        if self.view.sidebar.current_selected() == req_id:
            self.view.workspace.input_bar.set_running(False)
            self.view.workspace.info_panel.set_running(False)

    def _on_agent_finished(self, result):
        req_id = self._sender_req_id()
        # 只复位当前查看需求的运行态；后台结束的需求静默收尾
        if self.view.sidebar.current_selected() == req_id:
            self.view.workspace.input_bar.set_running(False)
            self.view.workspace.info_panel.set_running(False)
            self.view.workspace.chat_log.end_stream()
        # 整轮结束后刷新列表与当前需求状态（不改变用户正在查看的需求）
        self.view.sidebar.set_running_ids(set(self._workers.keys()))
        self.view._refresh()
        selected = self.view._current_req()
        if selected:
            self.view.workspace.set_req(self.view._store.get(selected.id))

    def _on_model_error(self, msg: str):
        req_id = self._sender_req_id()
        self.view.sidebar.set_running_ids(set(self._workers.keys()))
        if self.view.sidebar.current_selected() == req_id:
            self.view.workspace.input_bar.set_running(False)
            self.view.workspace.info_panel.set_running(False)
        from beehive.common.ui.dialogs.message import show_tip as tip

        tip(self.view, self.view.theme, msg)

    def _running_worker(self, req_id: str | None) -> DeziBeeWorker | None:
        """该需求正在运行的 worker（无则 None）。"""
        w = self._workers.get(req_id or "")
        return w if w is not None and w.isRunning() else None

    def _on_pause(self):
        req = self.view._current_req()
        worker = self._running_worker(req.id if req else None)
        if worker is None:
            from beehive.common.ui.dialogs.message import show_tip as tip

            tip(self.view, self.view.theme, "当前需求没有运行中的交互。")
            return
        worker.cancel()
        self.view.workspace.chat_log._render_event(
            {"kind": "info", "content": "用户请求暂停：正在终止当前交互。"}
        )
        if req is not None:
            self.view._append_conv_event(req.active_conversation(), "info", "用户请求暂停：正在终止当前交互。")
            self.view._persist(req)

    def shutdown(self):
        for worker in self._workers.values():
            if worker.isRunning():
                worker.cancel()
                worker.wait(2000)
        if any(worker.isRunning() for worker in self._workers.values()):
            return False
        self._workers.clear()
        from beehive.features.dezibee.core.preview import shutdown_server

        shutdown_server()
        return True

    def _on_approval_needed(self, pending):
        on_approval_needed(self.view, self._workers.get(self._sender_req_id() or ""), pending)

    def _on_ask_user_needed(self, payload):
        on_ask_user_needed(self.view, self._workers.get(self._sender_req_id() or ""), payload)
