"""单个项目列表项与点击信号。"""
from __future__ import annotations
from datetime import datetime
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout
from beehive.common.ui.theme import Theme
from beehive.features.wokbee.projects.models import Project
from beehive.features.wokbee.ui.project_status import STATUS_LABEL, STATUS_COLOR_KEY
class _ProjectItem(QFrame):
    clicked = Signal(str)
    context_menu = Signal(str, object)

    def __init__(self, project: Project, theme: Theme, selected: bool = False, parent=None):
        super().__init__(parent)
        self.project = project
        self.theme = theme
        self._selected = selected
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(58)
        self._build()

    def _build(self):
        c = self.theme.colors
        p = self.project
        bg = c["accent_light"] if self._selected else "transparent"
        border = (
            f"border-left: 3px solid {c['accent']};"
            if self._selected
            else "border-left: 3px solid transparent;"
        )
        self.setStyleSheet(f"""
            _ProjectItem {{
                background: {bg};
                border-radius: 6px;
                {border}
            }}
            _ProjectItem:hover {{ background: {c["subnav_hover"]}; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        top = QHBoxLayout()
        color = c[STATUS_COLOR_KEY.get(p.status, "text_hint")]
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {color}; font-size: 10px; background: transparent; border: none;")
        top.addWidget(dot)

        title = QLabel(p.title)
        title.setWordWrap(False)
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        title.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {c['text']};"
            "background: transparent; border: none;"
        )
        top.addWidget(title, 1)
        if p.pinned:
            pin = QLabel("📌")
            pin.setStyleSheet(
                "font-size: 11px; background: transparent; border: none;"
            )
            pin.setFixedWidth(16)
            top.addWidget(pin, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(top)

        try:
            dt = datetime.strptime(p.updated_at, "%Y-%m-%d %H:%M:%S")
            time_str = dt.strftime("%m-%d %H:%M")
        except ValueError:
            time_str = p.updated_at

        status = STATUS_LABEL.get(p.status, p.status.value)
        info = QLabel(f"{status} · {time_str}")
        info.setStyleSheet(
            f"font-size: 11px; color: {c['text_hint']}; background: transparent; border: none;"
        )
        layout.addWidget(info)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.project.id)
        elif event.button() == Qt.MouseButton.RightButton:
            self.context_menu.emit(self.project.id, event.globalPosition().toPoint())
        super().mousePressEvent(event)
