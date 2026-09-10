"""DeziBee 需求列表：左侧需求列表 + 新建/搜索。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from tokbee.ui.styles.theme import Theme

from dezibee.core.models import Requirement


class _ReqItem(QFrame):
    """需求列表单项（点击选中）。"""

    clicked = Signal(str)

    def __init__(self, req: Requirement, theme: Theme, selected: bool = False, parent=None):
        super().__init__(parent)
        self.req = req
        self.theme = theme
        self._selected = selected
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(58)
        self._build()

    def _build(self):
        c = self.theme.colors
        bg = c["card_bg"] if self._selected else "transparent"
        border = (
            f"border-left: 3px solid {c['accent']};"
            if self._selected
            else "border-left: 3px solid transparent;"
        )
        self.setStyleSheet(f"""
            _ReqItem {{ background: {bg}; border-radius: 6px; {border} }}
            _ReqItem:hover {{ background: {c["subnav_hover"]}; }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        top = QHBoxLayout()
        title = QLabel(self.req.title)
        title.setWordWrap(False)
        title.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {c['text']};"
            "background: transparent; border: none;"
        )
        top.addWidget(title, 1)
        layout.addLayout(top)

        mid = QHBoxLayout()
        chip = QLabel(self.req.id)
        chip.setStyleSheet(
            f"background: {c['tag_bg']}; color: {c['text_secondary']};"
            "border-radius: 4px; padding: 1px 6px; font-size: 10px;"
        )
        mid.addWidget(chip)
        mid.addStretch()
        layout.addLayout(mid)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.req.id)
        super().mousePressEvent(event)


class DeziBeeSidebar(QFrame):
    """左侧需求列表。"""

    req_selected = Signal(str)
    new_req = Signal()

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._reqs: list[Requirement] = []
        self._selected_id: str | None = None
        self._build()

    def _build(self):
        c = self.theme.colors
        self.setMinimumWidth(250)
        self.setMaximumWidth(340)
        self.setStyleSheet(f"""
            DeziBeeSidebar {{ background: {c["content_bg"]}; border-right: 1px solid {c["border"]}; }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        new_btn = QPushButton("＋ 新建需求")
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setFixedHeight(34)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_primary"]}; color: #ffffff;
                border: none; border-radius: 6px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {c["btn_primary_hover"]}; }}
        """)
        new_btn.clicked.connect(self.new_req.emit)
        layout.addWidget(new_btn)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索需求...")
        self._search.setFixedHeight(30)
        self._search.textChanged.connect(lambda: self.refresh())
        self._search.setStyleSheet(f"""
            QLineEdit {{
                background: {c["input_bg"]}; color: {c["text"]};
                border: 1px solid {c["input_border"]}; border-radius: 6px; padding: 0 8px;
            }}
        """)
        layout.addWidget(self._search)

        self._count = QLabel("")
        self._count.setStyleSheet(
            f"font-size: 11px; color: {c['text_hint']}; background: transparent; border: none;"
        )
        layout.addWidget(self._count)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(2)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._container)
        layout.addWidget(scroll, stretch=1)

    def set_reqs(self, reqs: list[Requirement]):
        self._reqs = list(reqs)
        self.refresh()

    def refresh(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        c = self.theme.colors
        kw = (self._search.text() or "").strip().lower()
        reqs = [
            r for r in self._reqs
            if not kw or kw in r.title.lower() or kw in r.id.lower()
        ]
        self._count.setText(f"{len(reqs)} 个需求")
        if not reqs:
            empty = QLabel("暂无需求，点击右上角「新建需求」开始")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(
                f"font-size: 12px; color: {c['text_hint']}; padding: 20px 0;"
            )
            self._list_layout.addWidget(empty)
            self._selected_id = None
            return

        found = False
        for r in reqs:
            if r.id == self._selected_id:
                found = True
            item = _ReqItem(r, self.theme, selected=(r.id == self._selected_id))
            item.clicked.connect(self._on_select)
            self._list_layout.addWidget(item)
        if self._selected_id and not found:
            self._selected_id = None

    def _on_select(self, req_id: str):
        self._selected_id = req_id
        self.refresh()
        self.req_selected.emit(req_id)

    def select(self, req_id: str):
        self._selected_id = req_id
        self.refresh()
        self.req_selected.emit(req_id)

    def select_first(self):
        """选中列表第一个需求（无则不动作）。"""
        if self._reqs:
            self.select(self._reqs[0].id)

    def current_selected(self) -> str | None:
        return self._selected_id

    def clear_selection(self):
        self._selected_id = None
        self.refresh()
