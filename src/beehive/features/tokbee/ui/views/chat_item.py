"""会话列表中的单行显示与鼠标事件。"""
from datetime import datetime
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy
from beehive.common.ui.theme import Theme
from beehive.common.ui.styles.labels import hint_label_qss, section_label_qss
from beehive.features.tokbee.core.chat_manager import ChatSession


class _ChatItem(QFrame):
    """对话列表中的单条项目。"""

    clicked = Signal(str)
    context_menu = Signal(str, object)  # (session_id, QPoint-global)

    def __init__(self, session: ChatSession, theme: Theme, selected: bool = False, parent=None):
        super().__init__(parent)
        self.session = session
        self.theme = theme
        self._selected = selected
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(58)
        self._build()

    def _build(self):
        c = self.theme.colors
        s = self.session

        bg = c["accent_light"] if self._selected else "transparent"
        border = f"border-left: 3px solid {c['accent']};" if self._selected else "border-left: 3px solid transparent;"
        self.setStyleSheet(f"""
            _ChatItem {{
                background: {bg};
                border-radius: 6px;
                {border}
            }}
            _ChatItem:hover {{
                background: {c["subnav_hover"]};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(4)
        if s.pinned:
            pin = QLabel("📌")
            pin.setStyleSheet("font-size: 11px;")
            pin.setFixedWidth(16)
            top.addWidget(pin, 0, Qt.AlignmentFlag.AlignLeft)

        title = QLabel(s.title)
        title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        title.setWordWrap(False)
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        title.setStyleSheet(section_label_qss(c))
        top.addWidget(title, 1)
        layout.addLayout(top)

        try:
            dt = datetime.strptime(s.updated_at, "%Y-%m-%d %H:%M:%S")
            time_str = dt.strftime("%m-%d %H:%M")
        except ValueError:
            time_str = s.updated_at

        info_parts = []
        if s.model_name:
            info_parts.append(s.model_name)
        info_parts.append(time_str)
        info_text = " · ".join(info_parts)

        info = QLabel(info_text)
        info.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        info.setWordWrap(False)
        info.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        info.setStyleSheet(hint_label_qss(c))
        layout.addWidget(info)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.session.id)
        elif event.button() == Qt.MouseButton.RightButton:
            self.context_menu.emit(self.session.id, event.globalPosition().toPoint())
        super().mousePressEvent(event)
