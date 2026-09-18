"""DeziBeeView：一级模块容器（左侧需求列表 + 右侧工作区），接线 Agent / 预览 / 总结 / 另起对话。"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from tokbee.ui.styles.theme import Theme

from wokbee.ui.ask_user_dialog import AskUserDialog
from wokbee.core.settings import WokBeeSettings

from dezibee.core.models import Conversation, Requirement
from dezibee.core.store import DeziBeeStore
from dezibee.core.services import DeziBeeWorker, build_design_prompt
from dezibee.ui.sidebar import DeziBeeSidebar
from dezibee.ui.workspace import DeziBeeWorkspace

logger = logging.getLogger("dezibee")


class NewReqDialog(QDialog):
    """新建需求对话框：只需输入需求名称，回车即创建。"""

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        c = theme.colors
        self.setWindowTitle("新建需求")
        self.setMinimumWidth(460)
        self.setStyleSheet(f"background: {c['content_bg']};")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(10)

        title_lbl = QLabel("需求名称")
        title_lbl.setStyleSheet(f"font-size: 13px; color: {c['text']};")
        layout.addWidget(title_lbl)
        self._title = QLineEdit()
        self._title.setPlaceholderText("例如：借款首页（详细需求可在发送消息时描述）")
        self._title.setFixedHeight(32)
        self._title.setStyleSheet(f"""
            QLineEdit {{
                background: {c['input_bg']}; color: {c['text']};
                border: 1px solid {c['input_border']}; border-radius: 6px; padding: 0 8px;
            }}
        """)
        layout.addWidget(self._title)

        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("取消")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setFixedSize(80, 32)
        cancel.setStyleSheet(f"""
            QPushButton {{
                background: {c['btn_bg']}; color: {c['text']};
                border: none; border-radius: 6px;
            }}
            QPushButton:hover {{ background: {c['btn_hover']}; }}
        """)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        ok = QPushButton("创建需求")
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setFixedSize(100, 32)
        ok.setStyleSheet(f"""
            QPushButton {{
                background: {c['btn_primary']}; color: #ffffff;
                border: none; border-radius: 6px;
            }}
            QPushButton:hover {{ background: {c['btn_primary_hover']}; }}
            QPushButton:disabled {{ background: {c['btn_bg']}; color: {c['text_hint']}; }}
        """)
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        layout.addLayout(row)

        # 回车即创建；名称为空时创建按钮禁用，避免误退出
        self._title.returnPressed.connect(ok.click)
        self._title.textChanged.connect(lambda t: ok.setEnabled(bool(t.strip())))
        ok.setEnabled(False)

    def result_data(self) -> str:
        return self._title.text().strip()


class DeziBeeView(QWidget):
    """DeziBee 一级模块：左侧需求列表 + 右侧工作区。"""

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._work_root = self._current_work_root()
        self._store = DeziBeeStore()
        self._reqs: list[Requirement] = []
        # 运行中的 worker，按需求 ID 隔离（支持多需求并行；事件只归属各自需求）
        self._workers: dict[str, DeziBeeWorker] = {}
        self._build()
        self._refresh()
        self.sidebar.select_first()

    @staticmethod
    def _current_work_root():
        """当前配置的 DeziBee 工作文件夹（设置页可能随时修改）。"""
        from wokbee.core.settings import WokBeeSettings

        return WokBeeSettings().dezibee_work_root

    def showEvent(self, event):
        """切回本页时：工作文件夹若被改过，重建 store 并重新加载需求列表。"""
        super().showEvent(event)
        root = self._current_work_root()
        if root != self._work_root:
            self._work_root = root
            self._store = DeziBeeStore(work_root=root)
            self.workspace.set_req(None)
            self._refresh()
            self.sidebar.select_first()

    def _build(self):
        self.setStyleSheet(f"background: {self.theme.colors['content_bg']};")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = DeziBeeSidebar(self.theme)
        self.sidebar.new_req.connect(self._on_new_req)
        self.sidebar.req_selected.connect(self._on_req_selected)
        self.sidebar.pin_requested.connect(self._on_pin_req)
        self.sidebar.rename_requested.connect(self._on_rename_req)
        self.sidebar.copy_id_requested.connect(self._on_copy_req_id)
        self.sidebar.delete_requested.connect(self._on_delete_req)
        layout.addWidget(self.sidebar)

        self.workspace = DeziBeeWorkspace(self.theme)
        layout.addWidget(self.workspace, 1)

        # 功能及用户输入区（输入框在上，按钮在下）
        bar = self.workspace.input_bar
        bar.preview_clicked.connect(self._on_preview)
        bar.export_clicked.connect(self._on_export)
        bar.new_conversation_clicked.connect(self._on_new_conversation)
        bar.open_folder_clicked.connect(self._on_open_folder)
        bar.pause_clicked.connect(self._on_pause)
        bar.model_changed.connect(self._on_model_changed)
        bar.send_clicked.connect(self._on_send)

    # ── 数据刷新 ─────────────────────────────────────────
    def _refresh(self):
        try:
            self._reqs = self._store.list()
        except Exception as e:
            logger.exception("读取需求失败")
            self._reqs = []
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, f"读取需求失败：{e}")
        self.sidebar.set_reqs(self._reqs, set(self._workers.keys()))

    def select_first(self):
        if self._reqs:
            self.sidebar.select(self._reqs[0].id)

    # ── 需求选择 ─────────────────────────────────────────
    def _on_req_selected(self, req_id: str):
        req = self._store.get(req_id)
        if req is None:
            self.workspace.set_req(None)
            return
        self.workspace.set_req(req)
        # 运行状态只反映当前查看的需求（其它需求可在后台并行运行）
        running = self._running_worker(req_id) is not None
        self.workspace.info_panel.set_running(running)
        self.workspace.input_bar.set_running(running)

    # ── 需求列表右键菜单：置顶 / 重命名 / 复制ID / 删除 ──
    def _on_pin_req(self, req_id: str):
        req = self._store.get(req_id)
        if req is None:
            return
        req.pinned = not req.pinned
        self._persist(req)
        self._refresh()

    def _on_rename_req(self, req_id: str):
        req = self._store.get(req_id)
        if req is None:
            return
        from wokbee.ui.dialogs import _ask_text

        name = _ask_text(
            self, self.theme, "重命名需求", "新的需求名称：", req.title, max_length=80
        )
        if not name or name == req.title:
            return
        req.title = name
        self._persist(req)
        self._refresh()
        if self.sidebar.current_selected() == req_id:
            self.workspace.set_req(self._store.get(req_id))

    def _on_copy_req_id(self, req_id: str):
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(req_id)
        from wokbee.ui.dialogs import tip

        tip(self, self.theme, f"已复制需求ID：{req_id}")

    def _on_delete_req(self, req_id: str):
        if self._running_worker(req_id) is not None:
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, "该需求正在处理中，请先暂停交互再删除需求。")
            return
        req = self._store.get(req_id)
        title = req.title if req else req_id
        from wokbee.ui.dialogs import _confirm

        if not _confirm(
            self,
            self.theme,
            "删除需求",
            f"确定删除需求「{title}」吗？\n"
            "该需求的工作文件夹（含 Demo / PRD / 对话记录）将被一并删除。",
        ):
            return
        self._store.delete(req_id)
        if self.sidebar.current_selected() == req_id:
            self.sidebar.clear_selection()
            self.workspace.set_req(None)
        self._refresh()
        self.sidebar.select_first()

    # ── 新建需求 ─────────────────────────────────────────
    def _on_new_req(self):
        dlg = NewReqDialog(self.theme, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        title = dlg.result_data()
        if not title:
            return  # 名称空时创建按钮已禁用，此处兜底
        try:
            req = self._store.create(title=title)
        except Exception as e:
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, f"创建需求失败：{e}")
            return
        self._refresh()
        self.sidebar.select(req.id)

    # ── 发送消息 → Agent ─────────────────────────────────
    def _on_send(self, text: str, attachments: list | None = None):
        req = self._current_req()
        if req is None:
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, "请先选择或创建一个需求。")
            return
        if self._running_worker(req.id) is not None:
            return  # 该需求已在处理中（其它需求不受影响，可并行）
        text = (text or "").strip()
        attachments = list(attachments or [])
        if not text and not attachments:
            return

        # 追加用户消息到当前对话（附件名一并记录，便于回看）
        conv = req.active_conversation()
        bubble_text = text
        if attachments:
            names = ", ".join(
                str(a.get("display_name") or a.get("path") or "附件")
                for a in attachments
            )
            bubble_text = f"{text}\n\n[附件] {names}" if text else f"（发送了附件：{names}）"
        self._append_conv_event(conv, "user", bubble_text)
        self.workspace.chat_log._append_bubble("user", bubble_text)
        self._persist(req)

        self._start_worker(req, text, attachments)

    def _start_worker(self, req: Requirement, text: str, attachments: list | None = None):
        # 运行态只作用于当前查看的需求（其它需求可同时运行）
        self.workspace.input_bar.set_running(True)
        self.workspace.info_panel.set_running(True)
        # 通知网页端新一轮开始（重置工具行配对与流式气泡状态）
        self.workspace.chat_log.begin_run()

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
        worker.model_error.connect(self._on_model_error)
        self._workers[req.id] = worker
        # 列表项立刻亮起「运行中」标识
        self.sidebar.set_running_ids(set(self._workers.keys()))
        worker.start()

    def _build_user_message(self, req: Requirement, text: str) -> str:
        prompt = build_design_prompt(req)
        return f"{prompt}\n\n【本轮指令】\n{text}"

    # ── Agent 事件回 UI（按需求隔离：落盘归属运行中的需求，渲染跟随当前查看） ──
    def _on_agent_event(self, kind: str, content: str, meta: dict):
        # 事件归属发出它的 worker 对应的需求，与当前左侧选中的需求无关
        req_id = self._sender_req_id()
        if req_id is None:
            return
        req = self._store.get(req_id)
        if req is None:
            return
        conv = req.active_conversation()
        meta = meta if isinstance(meta, dict) else {}
        target = "reasoning" if str(meta.get("target") or "") == "reasoning" else "text"
        if kind == "agent_stream":
            # 流式增量：只驱动网页实时气泡，不落盘（完整 agent 事件到达时前端自动定稿去重）
            if self.sidebar.current_selected() == req_id:
                self.workspace.chat_log.append_stream(target, content)
            return
        # 记录到归属需求的对话（agent/user/error/info/tool），工具事件保留结构化 meta
        if kind in ("agent", "user", "error", "info", "tool"):
            self._append_conv_event(conv, kind, content, meta)
            # 只有正在查看该需求时才渲染，避免对话串台
            if self.sidebar.current_selected() == req_id:
                self.workspace.chat_log._render_event(
                    {"kind": kind, "content": content, "meta": meta}
                )
            self._persist(req)

    def _sender_req_id(self) -> str | None:
        """当前信号发送者（worker）归属的需求 ID。"""
        sender = self.sender()
        for rid, w in self._workers.items():
            if w is sender:
                return rid
        return None

    def _on_ask_user_needed(self, payload: object):
        """主线程弹窗收集澄清答案，回传给发出请求的那个 worker。"""
        data = payload if isinstance(payload, dict) else {"type": "ask_user", "questions": []}
        dlg = AskUserDialog(data, self.theme, parent=self.window() or self)
        accepted = dlg.exec() == AskUserDialog.DialogCode.Accepted
        answers = dlg.result_payload() if accepted else {"cancelled": True}
        worker = self._workers.get(self._sender_req_id() or "")
        if worker is not None and worker.isRunning():
            worker.resolve_ask_user(answers)

    def _on_worker_stopped(self):
        # Custom result/model-error signals can arrive before QThread.run returns.
        req_id = self._sender_req_id()
        if req_id is not None:
            self._workers.pop(req_id, None)
        self.sidebar.set_running_ids(set(self._workers))
        if self.sidebar.current_selected() == req_id:
            self.workspace.input_bar.set_running(False)
            self.workspace.info_panel.set_running(False)

    def _on_agent_finished(self, result):
        req_id = self._sender_req_id()
        # 只复位当前查看需求的运行态；后台结束的需求静默收尾
        if self.sidebar.current_selected() == req_id:
            self.workspace.input_bar.set_running(False)
            self.workspace.info_panel.set_running(False)
            self.workspace.chat_log.end_stream()
        # 整轮结束后刷新列表与当前需求状态（不改变用户正在查看的需求）
        self.sidebar.set_running_ids(set(self._workers.keys()))
        self._refresh()
        selected = self._current_req()
        if selected:
            self.workspace.set_req(self._store.get(selected.id))

    def _on_model_error(self, msg: str):
        req_id = self._sender_req_id()
        self.sidebar.set_running_ids(set(self._workers.keys()))
        if self.sidebar.current_selected() == req_id:
            self.workspace.input_bar.set_running(False)
            self.workspace.info_panel.set_running(False)
        from wokbee.ui.dialogs import tip

        tip(self, self.theme, msg)

    # ── 事件落盘 ─────────────────────────────────────────
    def _append_conv_event(
        self, conv: Conversation, kind: str, content: str, meta: dict | None = None
    ):
        from wokbee.core.models import ProjectEvent

        ev = ProjectEvent(
            kind=kind,
            content=content,
            meta=dict(meta or {}),
        ).to_dict()
        conv.events.append(ev)
        conv.touch()

    def _persist(self, req: Requirement):
        try:
            self._store.save(req)
        except Exception as e:
            logger.exception("保存需求失败")
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, f"保存需求失败：{e}")

    def _current_req(self) -> Requirement | None:
        sid = self.sidebar.current_selected()
        if not sid:
            return None
        return self._store.get(sid)

    def _running_worker(self, req_id: str | None) -> DeziBeeWorker | None:
        """该需求正在运行的 worker（无则 None）。"""
        w = self._workers.get(req_id or "")
        return w if w is not None and w.isRunning() else None

    # ── 预览 ─────────────────────────────────────────────
    def _on_preview(self):
        req = self._current_req()
        if req is None:
            return
        from dezibee.core.preview import open_in_browser, req_url

        url = req_url(req.id)
        if not url:
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, "预览服务启动失败。")
            return
        ok = open_in_browser(url)
        if not ok:
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, f"无法打开浏览器。请手动访问：{url}")

    # ── 导出原型 ─────────────────────────────────────────
    def _on_export(self):
        req = self._current_req()
        if req is None:
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, "请先选择或创建一个需求。")
            return
        index = req.index_file
        if not index.is_file():
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, "当前需求还没有 Demo 原型（demo/index.html），暂时无法导出。")
            return
        from dezibee.core.packager import build_single_file

        # 先让用户选择保存位置，再打包写入（不默认塞进 demo/）
        from PySide6.QtWidgets import QFileDialog

        default_path = str(req.root / f"{req.id}.html")
        chosen, _ = QFileDialog.getSaveFileName(
            self, "导出原型", default_path, "HTML 文件 (*.html)"
        )
        if not chosen:
            return  # 用户取消

        try:
            html, warnings = build_single_file(req.demo_dir)
        except Exception as e:  # noqa: BLE001
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, f"导出失败：{e}")
            return

        out = Path(chosen)
        if out.suffix.lower() != ".html":
            out = out.with_suffix(".html")
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html, encoding="utf-8")
        except OSError as e:
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, f"写入导出文件失败：{e}")
            return

        from wokbee.ui.dialogs import tip

        mb = len(html.encode("utf-8")) / 1024 / 1024
        warn = ("\n\n注意：以下引用未内联（可能仍是外链）：\n" + "；".join(warnings)) if warnings else ""
        tip(
            self,
            self.theme,
            f"已导出原型（{mb:.2f} MB）：\n{out}\n\n"
            f"该文件自包含、零外部请求，直接上传 OSS 即可。{warn}",
        )

    # ── 打开需求文件夹 ───────────────────────────────────
    def _on_open_folder(self):
        req = self._current_req()
        if req is None:
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, "请先选择或创建一个需求。")
            return
        from wokbee.ui.dialogs import open_path

        req_dir = self._store.req_dir(req.id)
        req_dir.mkdir(parents=True, exist_ok=True)
        open_path(req_dir)

    # ── 暂停交互 ─────────────────────────────────────────
    def _on_pause(self):
        req = self._current_req()
        worker = self._running_worker(req.id if req else None)
        if worker is None:
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, "当前需求没有运行中的交互。")
            return
        worker.cancel()
        self.workspace.chat_log._render_event(
            {"kind": "info", "content": "用户请求暂停：正在终止当前交互。"}
        )
        if req is not None:
            self._append_conv_event(req.active_conversation(), "info", "用户请求暂停：正在终止当前交互。")
            self._persist(req)

    # ── 另起对话 ─────────────────────────────────────────
    def _on_new_conversation(self):
        req = self._current_req()
        if req is None:
            return
        conv = req.active_conversation()
        # 若当前对话没有任何记录，不重复开空对话
        if not conv.events and len(req.conversations) == 1:
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, "当前对话还没有记录，直接在原对话继续即可。")
            return
        new_conv = Conversation(
            title=f"对话 {len(req.conversations) + 1}",
        )
        req.conversations.append(new_conv)
        req.active_conv_id = new_conv.conv_id
        self._persist(req)
        self.workspace.set_req(req)
        from wokbee.ui.dialogs import tip

        tip(
            self,
            self.theme,
            "已另起对话（同一需求）。\n"
            "新对话不会继承旧聊天上下文，但仍可读取当前需求信息、Demo 和最新 PRD 文件。",
        )

    # ── 模型切换 ─────────────────────────────────────────
    def _on_model_changed(self, provider_id: str, model_id: str):
        req = self._current_req()
        if req is None:
            return
        req.provider = provider_id
        req.model_id = model_id
        self._persist(req)

    def shutdown(self):
        for worker in self._workers.values():
            if worker.isRunning():
                worker.cancel()
                worker.wait(2000)
        if any(worker.isRunning() for worker in self._workers.values()):
            return False
        self._workers.clear()
        from dezibee.core.preview import shutdown_server

        shutdown_server()
        return True
