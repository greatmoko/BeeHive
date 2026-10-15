"""会话列表的筛选、选中状态与行布局。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QScrollArea, QWidget, QLabel
from beehive.common.ui.theme import Theme
from beehive.common.ui.styles.buttons import apply_icon_btn
from beehive.common.ui.styles.inputs import apply_lineedit
from beehive.features.tokbee.core.chat_manager import ChatManager
from beehive.features.tokbee.ui.views.chat_item import _ChatItem
from beehive.features.tokbee.ui.views.chat_session_actions import ChatSessionActions


class _ChatSidebar(QFrame):
    session_selected = Signal(str)
    session_deleted = Signal()

    def __init__(self, theme: Theme, chat_manager: ChatManager, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.manager = chat_manager
        self._selected_id: str | None = None
        self.actions = ChatSessionActions(self)
        self._build()
        self.refresh()

    def _build(self):
        c = self.theme.colors
        self.setMinimumWidth(200)
        self.setMaximumWidth(240)
        self.setStyleSheet(f"""
            _ChatSidebar {{
                background: {c["subnav_bg"]};
                border-right: 1px solid {c["border"]};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 搜索框 + 新建按钮（同一行）
        search_row = QHBoxLayout()
        search_row.setSpacing(6)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索对话...")
        apply_lineedit(self._search, c)
        self._search.textChanged.connect(lambda: self.refresh())
        search_row.addWidget(self._search, stretch=1)

        new_btn = QPushButton("＋")
        new_btn.setToolTip("新建对话")
        apply_icon_btn(new_btn, c, size=34)
        new_btn.clicked.connect(self.actions._on_new)
        search_row.addWidget(new_btn)

        layout.addLayout(search_row)

        # 列表区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._list_container = QWidget()
        self._list_container.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(2)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll.setWidget(self._list_container)
        layout.addWidget(scroll, stretch=1)

    def refresh(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        keyword = self._search.text()
        sessions = self.manager.search(keyword)

        if not sessions:
            c = self.theme.colors
            empty = QLabel("暂无对话")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(f"font-size: 12px; color: {c['text_hint']}; padding: 20px 0;")
            self._list_layout.addWidget(empty)
            return

        for s in sessions:
            item = _ChatItem(s, self.theme, selected=(s.id == self._selected_id))
            item.clicked.connect(self.select)
            item.context_menu.connect(self.actions._on_context_menu)
            self._list_layout.addWidget(item)

    def select(self, session_id: str):
        self._selected_id = session_id
        self.refresh()
        self.session_selected.emit(session_id)
