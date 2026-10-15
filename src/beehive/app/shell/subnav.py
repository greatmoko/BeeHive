"""共享二级导航组件，供 configuration 与 settings 两个视图复用。"""
from __future__ import annotations
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QFrame, QPushButton
from beehive.common.ui.theme import Theme


class _SubNavButton(QPushButton):
    """二级导航按钮：左侧图标 + 右侧文字。"""

    def __init__(self, icon_text: str, label: str, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._active = False
        self._icon_text = icon_text
        self._label = label
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(36)
        self.setText(f"  {icon_text}   {label}")
        self._apply_style()

    def _apply_style(self):
        c = self._theme.colors
        if self._active:
            bg = c["subnav_active"]
            tc = c["subnav_text_active"]
            fw = "bold"
        else:
            bg = "transparent"
            tc = c["subnav_text"]
            fw = "normal"
        self.setStyleSheet(f"""
            QPushButton {{
                background: {bg}; color: {tc}; font-weight: {fw};
                border: none; border-radius: 6px;
                padding: 0 12px; text-align: left; font-size: 13px;
                text-decoration: none; outline: none;
            }}
            QPushButton:hover {{ background: {c["subnav_hover"]}; }}
            QPushButton:focus {{ outline: none; border: none; }}
        """)

    def set_active(self, active: bool):
        self._active = active
        self._apply_style()


class _SubNav(QFrame):
    """设置页的二级导航栏。"""

    nav_changed = Signal(str)

    def __init__(self, theme: Theme, *, object_name: str = "subNav", parent=None):
        super().__init__(parent)
        self.theme = theme
        self._buttons: dict[str, _SubNavButton] = {}
        self._current = ""
        self._object_name = object_name
        self._build()

    def _build(self):
        c = self.theme.colors
        self.setMinimumWidth(180)
        self.setMaximumWidth(220)
        self.setObjectName(self._object_name)
        self.setStyleSheet(f"""
            QFrame#{self._object_name} {{
                background: {c["subnav_bg"]};
                border: none;
                border-right: 1px solid {c["border"]};
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(2)
        for nav_id, icon, label in self.ITEMS:
            btn = _SubNavButton(icon, label, self.theme)
            btn.clicked.connect(lambda _, nid=nav_id: self._on_click(nid))
            layout.addWidget(btn)
            self._buttons[nav_id] = btn
        layout.addStretch()

    def _on_click(self, nav_id: str):
        if nav_id == self._current:
            return
        self._current = nav_id
        for nid, btn in self._buttons.items():
            btn.set_active(nid == nav_id)
        self.nav_changed.emit(nav_id)

    def select(self, nav_id: str):
        self._on_click(nav_id)
