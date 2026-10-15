"""按职责组织的 card。"""
from __future__ import annotations
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QCheckBox
from beehive.common.ui.theme import Theme
from beehive.system.mcp.mcp_store import McpServerConfig


class _McpCard(QFrame):
    toggled = Signal(str, bool)
    edit_clicked = Signal(str)
    delete_clicked = Signal(str)
    test_clicked = Signal(str)

    def __init__(self, server: McpServerConfig, theme: Theme, parent=None):
        super().__init__(parent)
        self.server = server
        self.theme = theme
        c = theme.colors
        self.setObjectName("mcpCard")
        self.setStyleSheet(f"""
            QFrame#mcpCard {{
                background: {c["card_bg"]};
                border: 1px solid {c["border_light"]};
                border-radius: 8px;
            }}
            QFrame#mcpCard QLabel, QFrame#mcpCard QCheckBox {{
                background: transparent; border: none;
            }}
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)

        info = QVBoxLayout()
        title = QLabel(server.name)
        title.setWordWrap(True)
        title.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {c['text']}; background: transparent; border: none;")
        info.addWidget(title)
        # stdio 的命令/参数路径不展示，仅保留服务器名称，避免撑宽卡片出横向滚动条
        if server.transport != "stdio":
            detail = f"{server.transport} · {server.url}"
            desc = QLabel(detail)
            desc.setWordWrap(True)
            desc.setStyleSheet(f"font-size: 12px; color: {c['text_secondary']}; background: transparent; border: none;")
            info.addWidget(desc)
        lay.addLayout(info, stretch=1)

        from beehive.common.ui.styles.checks import checkbox_qss
        from beehive.common.ui.styles.buttons import secondary_btn_qss
        chk = QCheckBox("启用")
        chk.setChecked(server.enabled)
        chk.setStyleSheet(checkbox_qss(c))
        chk.toggled.connect(lambda v: self.toggled.emit(server.id, v))
        lay.addWidget(chk)

        for text, sig in (("测试", self.test_clicked), ("编辑", self.edit_clicked), ("删除", self.delete_clicked)):
            btn = QPushButton(text)
            btn.setFixedHeight(30)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setAutoDefault(False)
            btn.setDefault(False)
            if text == "删除":
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: transparent; color: {c["danger"]};
                        border: 1px solid {c["border"]}; border-radius: 6px;
                        padding: 0 10px; text-decoration: none;
                    }}
                    QPushButton:hover {{ background: {c["danger_light"]}; }}
                """)
            else:
                btn.setStyleSheet(secondary_btn_qss(c))
            btn.clicked.connect(lambda _, sid=server.id, s=sig: s.emit(sid))
            lay.addWidget(btn)
