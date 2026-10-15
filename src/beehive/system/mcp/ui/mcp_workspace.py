"""AI 配置 — MCP 服务器管理。"""

from __future__ import annotations

import json
import shlex

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QLineEdit, QDialog, QCheckBox, QComboBox,
    QTextEdit,
)

from beehive.common.ui.theme import Theme
from beehive.common.ui.styles.buttons import apply_primary_btn
from beehive.system.mcp.mcp_store import McpServerConfig, McpStore
from beehive.common.ui.dialogs.message import show_tip as _tip, _confirm


from beehive.system.mcp.ui.card import _McpCard


from beehive.system.mcp.ui.editor import _McpEditor


class McpWorkspace(QWidget):
    def __init__(self, theme: Theme, store: McpStore | None = None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.store = store or McpStore()
        self._test_workers: list[_McpTestWorker] = []  # 持有引用，避免后台测试线程被 GC
        self._build()
        self.refresh()

    def _build(self):
        c = self.theme.colors
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        header = QFrame()
        header.setStyleSheet(f"background: {c['content_bg']}; border: none;")
        hl = QVBoxLayout(header)
        hl.setContentsMargins(28, 20, 28, 12)
        title = QLabel("MCP")
        title.setStyleSheet(
            f"font-size: 20px; font-weight: bold; color: {c['text']};"
            "background: transparent; border: none;"
        )
        hl.addWidget(title)
        tip = QLabel(
            "配置 Model Context Protocol 服务器。启用后，WokBee 运行时会加载其工具供 Agent 调用。"
            "stdio 需本机已安装对应命令（如 npx）。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(
            f"font-size: 12px; color: {c['text_hint']};"
            "background: transparent; border: none;"
        )
        hl.addWidget(tip)
        root.addWidget(header)

        bar = QHBoxLayout()
        bar.setContentsMargins(28, 12, 28, 8)
        bar.addStretch()
        add_btn = QPushButton("添加 MCP")
        apply_primary_btn(add_btn, c)
        add_btn.clicked.connect(self._on_add)
        bar.addWidget(add_btn)
        root.addLayout(bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self._list = QWidget()
        self._list_layout = QVBoxLayout(self._list)
        self._list_layout.setContentsMargins(28, 8, 28, 20)
        self._list_layout.setSpacing(8)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._list)
        root.addWidget(scroll, stretch=1)

    def refresh(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        servers = self.store.list_servers()
        c = self.theme.colors
        if not servers:
            empty = QLabel("暂无 MCP。可添加例如：npx -y @modelcontextprotocol/server-filesystem <目录>")
            empty.setWordWrap(True)
            empty.setStyleSheet(f"color: {c['text_hint']}; padding: 20px;")
            self._list_layout.addWidget(empty)
            return
        for s in servers:
            card = _McpCard(s, self.theme)
            card.toggled.connect(self._on_toggle)
            card.edit_clicked.connect(self._on_edit)
            card.delete_clicked.connect(self._on_delete)
            card.test_clicked.connect(self._on_test)
            self._list_layout.addWidget(card)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def _on_add(self):
        dlg = _McpEditor(self.theme, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        server = dlg.result_server()
        if not server:
            _tip(self, self.theme, "名称不能为空，或环境变量 JSON 无效。")
            return
        if not server.to_connection():
            _tip(self, self.theme, "请完整填写命令（stdio）或 URL（SSE/HTTP）。")
            return
        self.store.upsert(server)
        self.refresh()

    def _on_edit(self, server_id: str):
        server = next((s for s in self.store.list_servers() if s.id == server_id), None)
        if not server:
            return
        dlg = _McpEditor(self.theme, server, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dlg.result_server()
        if not updated or not updated.to_connection():
            _tip(self, self.theme, "配置不完整或环境变量 JSON 无效。")
            return
        self.store.upsert(updated)
        self.refresh()

    def _on_toggle(self, server_id: str, enabled: bool):
        self.store.set_enabled(server_id, enabled)

    def _on_delete(self, server_id: str):
        if not _confirm(self, self.theme, "删除 MCP", "确定删除该 MCP 配置？"):
            return
        self.store.delete(server_id)
        self.refresh()

    def _on_test(self, server_id: str):
        server = next((s for s in self.store.list_servers() if s.id == server_id), None)
        if not server:
            return
        conn = server.to_connection()
        if not conn:
            _tip(self, self.theme, "配置不完整，无法连接。")
            return
        # 后台线程测试连接，避免主线程 asyncio.run 无超时卡死窗口。
        worker = _McpTestWorker(server, parent=self)
        self._test_workers.append(worker)
        worker.result.connect(lambda msg: _tip(self, self.theme, msg))
        worker.result.connect(lambda *_: self._test_workers.remove(worker) if worker in self._test_workers else None)
        worker.finished.connect(worker.deleteLater)
        worker.start()


from beehive.system.mcp.ui.test_worker import _McpTestWorker
