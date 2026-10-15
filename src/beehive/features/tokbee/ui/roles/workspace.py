"""按职责组织的 workspace。"""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QFrame, QLabel, QPushButton, QScrollArea, QDialog
from beehive.common.ui.theme import Theme
from beehive.common.ui.dialogs.message import _confirm
from beehive.common.ui.styles.buttons import apply_primary_btn
from beehive.features.tokbee.core.ai_role import AIRole, AIRoleManager
from beehive.features.tokbee.ui.roles.card import _AIRoleCard
from beehive.features.tokbee.ui.roles.editor import _AIRoleDialog


class _AIRoleWorkspace(QWidget):
    """AI 角色管理工作区。"""

    def __init__(self, theme: Theme, role_manager: AIRoleManager, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.role_manager = role_manager
        self._build()

    def _build(self):
        c = self.theme.colors

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setFixedHeight(56)
        header.setStyleSheet(f"background: {c['content_bg']}; border: none;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 0, 24, 0)

        title = QLabel("AI 角色")
        title.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {c['text']};"
            "background: transparent; border: none;"
        )
        header_layout.addWidget(title)
        header_layout.addStretch()

        add_btn = QPushButton("+ 新建角色")
        apply_primary_btn(add_btn, c)
        add_btn.clicked.connect(self._on_add)
        header_layout.addWidget(add_btn)

        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(24, 16, 24, 16)
        self._list_layout.setSpacing(8)
        self._list_layout.addStretch()

        scroll.setWidget(self._list_container)
        outer.addWidget(scroll)

        self._refresh()

    def _refresh(self):
        while self._list_layout.count() > 0:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        roles = self.role_manager.list_all()
        if not roles:
            c = self.theme.colors
            empty = QLabel("暂无 AI 角色")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(f"color: {c['text_hint']}; font-size: 14px; padding: 40px;")
            self._list_layout.addWidget(empty)
        else:
            for role in roles:
                card = _AIRoleCard(role, self.theme)
                card.action_triggered.connect(self._on_card_action)
                self._list_layout.addWidget(card)

        self._list_layout.addStretch()

    def _on_card_action(self, action: str, role_id: str):
        if action == "edit":
            role = self.role_manager.get(role_id)
            if not role:
                return
            dlg = _AIRoleDialog(self.theme, role, self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.role_manager.update(role_id, **dlg.get_data())
                self._refresh()
        elif action == "delete":
            self._confirm_delete(role_id)

    def _on_add(self):
        dlg = _AIRoleDialog(self.theme, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            role = AIRole(**data)
            self.role_manager.add(role)
            self._refresh()

    def _confirm_delete(self, role_id: str):
        role = self.role_manager.get(role_id)
        if not role:
            return
        if not _confirm(self, self.theme, "确认删除",
                        f"确定要删除角色「{role.name}」吗？\n此操作不可撤销。"):
            return
        self.role_manager.delete(role_id)
        self._refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh()
