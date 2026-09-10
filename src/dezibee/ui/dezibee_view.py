"""DeziBeeView：一级模块容器（左侧需求列表 + 右侧工作区），接线 Agent / 预览 / 总结 / 另起对话。"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from tokbee.ui.styles.theme import Theme

from dezibee.core.models import Conversation, Requirement
from dezibee.core.store import DeziBeeStore
from dezibee.core.services import DeziBeeWorker, build_design_prompt
from dezibee.ui.sidebar import DeziBeeSidebar
from dezibee.ui.workspace import DeziBeeWorkspace

logger = logging.getLogger("dezibee")


class NewReqDialog(QDialog):
    """新建需求对话框：名称 + 描述。"""

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
        self._title.setPlaceholderText("例如：借款首页")
        self._title.setFixedHeight(32)
        self._title.setStyleSheet(f"""
            QLineEdit {{
                background: {c['input_bg']}; color: {c['text']};
                border: 1px solid {c['input_border']}; border-radius: 6px; padding: 0 8px;
            }}
        """)
        layout.addWidget(self._title)

        desc_lbl = QLabel("需求描述")
        desc_lbl.setStyleSheet(f"font-size: 13px; color: {c['text']};")
        layout.addWidget(desc_lbl)
        self._desc = QTextEdit()
        self._desc.setPlaceholderText("描述你想设计的产品 / 页面，例如：一个贷款 App 的首页，包含额度、借款按钮…")
        self._desc.setFixedHeight(110)
        self._desc.setStyleSheet(f"""
            QTextEdit {{
                background: {c['input_bg']}; color: {c['text']};
                border: 1px solid {c['input_border']}; border-radius: 6px; padding: 6px 8px;
            }}
        """)
        layout.addWidget(self._desc)

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
        """)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        layout.addLayout(row)

    def result_data(self) -> tuple[str, str]:
        return (
            self._title.text().strip(),
            self._desc.toPlainText().strip(),
        )


class DeziBeeView(QWidget):
    """DeziBee 一级模块：左侧需求列表 + 右侧工作区。"""

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._work_root = self._current_work_root()
        self._store = DeziBeeStore()
        self._reqs: list[Requirement] = []
        self._worker: DeziBeeWorker | None = None
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
        layout.addWidget(self.sidebar)

        self.workspace = DeziBeeWorkspace(self.theme)
        layout.addWidget(self.workspace, 1)

        # 功能区
        bar = self.workspace.action_bar
        bar.preview_clicked.connect(self._on_preview)
        bar.summarize_clicked.connect(self._on_summarize)
        bar.new_conversation_clicked.connect(self._on_new_conversation)
        bar.model_changed.connect(self._on_model_changed)

        # 输入区
        self.workspace.input_bar.send_clicked.connect(self._on_send)

    # ── 数据刷新 ─────────────────────────────────────────
    def _refresh(self):
        try:
            self._reqs = self._store.list()
        except Exception as e:
            logger.exception("读取需求失败")
            self._reqs = []
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, f"读取需求失败：{e}")
        self.sidebar.set_reqs(self._reqs)

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

    # ── 新建需求 ─────────────────────────────────────────
    def _on_new_req(self):
        dlg = NewReqDialog(self.theme, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        title, desc = dlg.result_data()
        if not title:
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, "请输入需求名称。")
            return
        try:
            req = self._store.create(title=title, description=desc)
        except Exception as e:
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, f"创建需求失败：{e}")
            return
        self._refresh()
        self.sidebar.select(req.id)

    # ── 发送消息 → Agent ─────────────────────────────────
    def _on_send(self, text: str):
        req = self._current_req()
        if req is None:
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, "请先选择或创建一个需求。")
            return
        if self._worker is not None and self._worker.isRunning():
            return  # 已在处理中

        # 追加用户消息到当前对话
        conv = req.active_conversation()
        self._append_conv_event(conv, "user", text)
        self.workspace.chat_log._append_bubble("user", text)
        self._persist(req)

        self._start_worker(req, text)

    def _start_worker(self, req: Requirement, text: str):
        self.workspace.input_bar.set_running(True)
        self.workspace.action_bar.set_running(True)
        self.workspace.action_bar.setEnabled(False)

        worker = DeziBeeWorker(
            req,
            self._build_user_message(req, text),
            parent=self,
        )
        worker.event_emitted.connect(self._on_agent_event)
        worker.finished_result.connect(self._on_agent_finished)
        self._worker = worker
        worker.start()

    def _build_user_message(self, req: Requirement, text: str) -> str:
        conv = req.active_conversation()
        prompt = build_design_prompt(req, conv)
        return f"{prompt}\n\n【本轮指令】\n{text}"

    # ── Agent 事件回 UI（交互记录区） ────────────────────
    def _on_agent_event(self, kind: str, content: str, meta: dict):
        req = self._current_req()
        if req is None:
            return
        conv = req.active_conversation()
        meta = meta if isinstance(meta, dict) else {}
        target = "reasoning" if str(meta.get("target") or "") == "reasoning" else "text"
        if kind == "agent_stream":
            self.workspace.chat_log.append_stream(content, target)
            return
        # 记录到当前对话（agent/user/error/info/tool）
        if kind in ("agent", "user", "error", "info", "tool"):
            self._append_conv_event(conv, kind, content)
            phase = str(meta.get("phase") or "")
            if kind == "agent" and phase in ("reasoning", "narration", "answer"):
                # 完整事件到达：把流式气泡原地定稿，避免同一段内容渲染两遍
                bubble_target = "reasoning" if phase == "reasoning" else "text"
                if self.workspace.chat_log.finalize_stream(bubble_target, content):
                    self._persist(req)
                    return
            self.workspace.chat_log._render_event(
                {"kind": kind, "content": content}
            )
            self._persist(req)

    def _on_agent_finished(self, result):
        self._worker = None
        self.workspace.input_bar.set_running(False)
        self.workspace.action_bar.set_running(False)
        self.workspace.action_bar.setEnabled(True)
        self.workspace.chat_log.end_stream()
        # 关键：整轮结束后重新加载需求，确保界面状态最新
        self._refresh()
        selected = self._current_req()
        if selected:
            self.workspace.set_req(self._store.get(selected.id))

    def _on_model_error(self, msg: str):
        self._worker = None
        self.workspace.input_bar.set_running(False)
        self.workspace.action_bar.set_running(False)
        self.workspace.action_bar.setEnabled(True)
        from wokbee.ui.dialogs import tip

        tip(self, self.theme, msg)

    # ── 事件落盘 ─────────────────────────────────────────
    def _append_conv_event(self, conv: Conversation, kind: str, content: str):
        from wokbee.core.models import ProjectEvent

        ev = ProjectEvent(
            kind=kind,
            content=content,
            meta={},
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

    # ── 总结上下文 ───────────────────────────────────────
    def _on_summarize(self):
        req = self._current_req()
        if req is None:
            return
        conv = req.active_conversation()
        if not conv.events:
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, "当前对话还没有交互记录，暂无可总结内容。")
            return
        self.workspace.input_bar.set_running(True)
        from dezibee.core.services import summarize_context

        try:
            summary = summarize_context(req, conversation=conv)
        except Exception as e:
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, f"总结失败：{e}")
            self.workspace.input_bar.set_running(False)
            return
        req.context_summary = summary
        conv.summary = summary
        self._persist(req)
        self.workspace.input_bar.set_running(False)
        from wokbee.ui.dialogs import tip

        tip(
            self,
            self.theme,
            "上下文已总结（未删除原始交互记录）。\n"
            "新的对话会优先使用此 Design Context 继续设计。",
        )

    # ── 另起对话 ─────────────────────────────────────────
    def _on_new_conversation(self):
        req = self._current_req()
        if req is None:
            return
        conv = req.active_conversation()
        # 若当前对话没有任何记录，不重复开空对话
        if not conv.events and req.context_summary == conv.summary and len(req.conversations) == 1:
            from wokbee.ui.dialogs import tip

            tip(self, self.theme, "当前对话还没有记录，直接在原对话继续即可。")
            return
        new_conv = Conversation(
            title=f"对话 {len(req.conversations) + 1}",
            summary=req.context_summary or "",
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
            "新对话将继承需求信息、当前 Demo/PRD 文件与最新上下文摘要。",
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
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)
        from dezibee.core.preview import shutdown_server

        shutdown_server()
