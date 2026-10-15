"""我的厂商列表控件，只管理列表显示和选择。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget, QListWidgetItem

class ProviderSidebar(QFrame):
    add_requested = Signal()
    provider_selected = Signal(str)


    def __init__(self, theme, parent=None):
        super().__init__(parent)
        c = theme.colors
        left = self
        left.setObjectName("providerSideList")
        left.setFixedWidth(220)
        left.setStyleSheet(f"""
            QFrame#providerSideList {{
                background: {c["subnav_bg"]};
                border: none;
                border-right: 1px solid {c["border"]};
            }}
        """)
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(8, 12, 8, 12)
        left_l.setSpacing(6)

        head_row = QHBoxLayout()
        head_row.setContentsMargins(4, 0, 4, 0)
        head = QLabel("我的厂商")
        head.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {c['text']};"
            "background: transparent; border: none;"
        )
        head_row.addWidget(head)
        head_row.addStretch()

        add_btn = QPushButton("+")
        add_btn.setToolTip("添加厂商")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setFixedSize(28, 28)
        add_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_bg"]}; color: {c["accent"]};
                border: none; border-radius: 6px; font-size: 16px; font-weight: bold;
            }}
            QPushButton:hover {{ background: {c["btn_hover"]}; }}
        """)
        add_btn.clicked.connect(self.add_requested)
        head_row.addWidget(add_btn)
        left_l.addLayout(head_row)

        self._list = QListWidget()
        self._list.setStyleSheet(f"""
            QListWidget {{
                background: transparent; border: none; outline: none;
                color: {c["text"]}; font-size: 13px;
            }}
            QListWidget::item {{
                padding: 8px 10px; border-radius: 6px;
            }}
            QListWidget::item:selected {{
                background: {c["subnav_active"]}; color: {c["subnav_text_active"]};
            }}
            QListWidget::item:hover {{
                background: {c["subnav_hover"]};
            }}
        """)
        self._list.currentItemChanged.connect(self._selection_changed)
        left_l.addWidget(self._list, stretch=1)

        empty_hint = QLabel("点击右上角 + 添加厂商")
        empty_hint.setWordWrap(True)
        empty_hint.setStyleSheet(f"font-size: 11px; color: {c['text_hint']}; padding: 4px 8px;")
        self._left_empty = empty_hint
        left_l.addWidget(empty_hint)

    def _selection_changed(self, current, _previous):
        self.provider_selected.emit(current.data(Qt.ItemDataRole.UserRole) if current else "")

    def render(self, entries, current_id=""):
        self._list.blockSignals(True)
        self._list.clear()
        for pid, name, icon, _family in entries:
            item = QListWidgetItem(f"{icon}  {name}")
            item.setData(Qt.ItemDataRole.UserRole, pid)
            self._list.addItem(item)
        self._list.blockSignals(False)
        self._left_empty.setVisible(self._list.count() == 0)
        if current_id and self.select(current_id):
            return True
        self._list.clearSelection()
        return False

    def select(self, provider_id):
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole) == provider_id:
                self._list.setCurrentRow(i)
                return True
        return False

    def clear_selection(self):
        self._list.clearSelection()
