"""按职责组织的 card。"""
from __future__ import annotations
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QFrame, QLabel, QPushButton
from beehive.common.ui.theme import Theme
from beehive.features.tokbee.core.ai_role import AIRole


class _AIRoleCard(QFrame):
    """单条 AI 角色卡片。"""

    action_triggered = Signal(str, str)  # (action, role_id)

    def __init__(self, role: AIRole, theme: Theme, parent=None):
        super().__init__(parent)
        self.role = role
        self.theme = theme
        self._build()

    def _build(self):
        c = self.theme.colors
        r = self.role

        self.setStyleSheet(f"""
            _AIRoleCard {{
                background: {c["card_bg"]};
                border-radius: 8px;
                border: 1px solid {c["border_light"]};
            }}
            _AIRoleCard:hover {{
                background: {c["card_hover"]};
                border-color: {c["border"]};
            }}
        """)
        self.setFixedHeight(72)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)

        info = QVBoxLayout()
        info.setSpacing(3)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_label = QLabel(r.name)
        name_label.setStyleSheet(f"background: transparent; font-size: 14px; font-weight: bold; color: {c['text']};")
        name_row.addWidget(name_label)

        if r.is_default:
            tag = QLabel("默认")
            tag.setStyleSheet(f"""
                font-size: 10px; color: {c["accent"]};
                background: {c["accent_light"]}; border-radius: 3px;
                padding: 1px 5px;
            """)
            name_row.addWidget(tag)

        name_row.addStretch()
        info.addLayout(name_row)

        preview = r.description[:80].replace("\n", " ") if r.description else "(无描述)"
        desc_label = QLabel(preview)
        desc_label.setStyleSheet(f"background: transparent; font-size: 12px; color: {c['text_secondary']};")
        desc_label.setWordWrap(False)
        desc_label.setMinimumWidth(0)
        from PySide6.QtWidgets import QSizePolicy
        desc_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        info.addWidget(desc_label)

        layout.addLayout(info, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        edit_btn = QPushButton("编辑")
        edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        edit_btn.setFixedSize(52, 26)
        edit_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_bg"]}; color: {c["text_secondary"]};
                border: none; border-radius: 4px; font-size: 11px;
            }}
            QPushButton:hover {{ background: {c["btn_hover"]}; }}
        """)
        edit_btn.clicked.connect(lambda: self.action_triggered.emit("edit", self.role.id))
        btn_row.addWidget(edit_btn)

        if not r.is_default:
            del_btn = QPushButton("删除")
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.setFixedSize(52, 26)
            del_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {c["btn_bg"]}; color: {c["danger"]};
                    border: none; border-radius: 4px; font-size: 11px;
                }}
                QPushButton:hover {{ background: {c["btn_hover"]}; }}
            """)
            del_btn.clicked.connect(lambda: self.action_triggered.emit("delete", self.role.id))
            btn_row.addWidget(del_btn)

        layout.addLayout(btn_row)
