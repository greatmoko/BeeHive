"""按职责组织的 navigation。"""
from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QVBoxLayout, QLabel, QPushButton, QFrame
from beehive.common.ui.theme import Theme
_RESOURCES = Path(__file__).resolve().parents[2] / "common/ui/resources"


class _ClickableLabel(QLabel):
    """可点击的文字标签，与上方图标按钮触发同一回调。"""

    def __init__(self, text: str, on_click, parent=None):
        super().__init__(text, parent)
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click()
        super().mousePressEvent(event)


class _PrimaryNav(QFrame):
    """最左侧一级导航栏 — 数据驱动，循环构建。"""

    nav_changed = Signal(str)

    # (nav_id, icon_key, label_text, font_size, position)
    # icon_key: "chat" 消息图标 / "logo" 蜜蜂 logo / 其它为 emoji 文本
    # position: "top" 正常顺序, "bottom" 贴底
    NAV_ITEMS = [
        ("chat",       "chat",  "TokBee",  18, "top"),
        ("wokbee",     "🐝",    "WokBee",  18, "top"),
        ("autobee",    "⏰",    "AutoBee", 18, "top"),
        ("dezibee",    "🎨",    "DeziBee", 18, "top"),
        ("automation", "⚡",    "AIConfig",  20, "top"),
        ("settings",   "⚙",    "Settings",    22, "bottom"),
    ]

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._current = ""
        self._buttons: dict[str, QPushButton] = {}
        self._labels: dict[str, QLabel] = {}
        self._font_sizes: dict[str, int] = {}
        self._build()

    def _build(self):
        c = self.theme.colors
        self.setFixedWidth(56)
        self.setObjectName("primaryNav")
        self.setStyleSheet(f"""
            QFrame#primaryNav {{
                background: {c["sidebar_bg"]};
                border: none;
                border-right: 1px solid {c["border"]};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        top_items = [i for i in self.NAV_ITEMS if i[4] == "top"]
        bottom_items = [i for i in self.NAV_ITEMS if i[4] == "bottom"]

        for idx, (nav_id, icon_text, label_text, font_size, _) in enumerate(top_items):
            if idx > 0:
                layout.addSpacing(6)
            btn, lbl = self._create_nav_button(nav_id, icon_text, label_text, font_size)
            layout.addLayout(self._make_nav_item(btn, lbl))

        layout.addStretch()

        for nav_id, icon_text, label_text, font_size, _ in bottom_items:
            btn, lbl = self._create_nav_button(nav_id, icon_text, label_text, font_size)
            layout.addLayout(self._make_nav_item(btn, lbl))

        self._apply_styles()

    def _create_nav_button(self, nav_id: str, icon_key: str | None,
                           label_text: str, font_size: int) -> tuple[QPushButton, QLabel]:
        btn = QPushButton()
        btn.setFixedSize(40, 40)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(label_text)
        btn.setAutoDefault(False)
        btn.setDefault(False)

        if icon_key == "logo":
            logo_path = _RESOURCES / "logo.png"
            if logo_path.exists():
                pm = QPixmap(str(logo_path)).scaled(
                    30, 30,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                btn.setIcon(QIcon(pm))
                btn.setIconSize(QSize(30, 30))
            else:
                btn.setText("🐝")
        elif icon_key == "chat":
            chat_path = _RESOURCES / "chat.png"
            if chat_path.exists():
                pm = QPixmap(str(chat_path)).scaled(
                    28, 28,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                btn.setIcon(QIcon(pm))
                btn.setIconSize(QSize(28, 28))
            else:
                btn.setText("💬")
        elif icon_key:
            btn.setText(icon_key)
        else:
            btn.setText("•")

        btn.clicked.connect(lambda _, nid=nav_id: self._on_click(nid))

        lbl = _ClickableLabel(label_text, lambda nid=nav_id: self._on_click(nid))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("background: transparent; border: none;")

        self._buttons[nav_id] = btn
        self._labels[nav_id] = lbl
        self._font_sizes[nav_id] = font_size
        return btn, lbl

    @staticmethod
    def _make_nav_item(btn: QPushButton, label: QLabel) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        col.addWidget(btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(label, alignment=Qt.AlignmentFlag.AlignHCenter)
        return col

    def _apply_styles(self):
        c = self.theme.colors
        # 与二级导航一致：浅绿底 + 绿色文字
        for nav_id, btn in self._buttons.items():
            active = self._current == nav_id
            bg = c["sidebar_active"] if active else "transparent"
            color = c["sidebar_text_active"] if active else c["sidebar_text"]
            fs = self._font_sizes.get(nav_id, 20)

            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {bg};
                    color: {color};
                    border: none;
                    border-radius: 8px;
                    font-size: {fs}px;
                    outline: none;
                }}
                QPushButton:hover {{
                    background: {c["sidebar_hover"]};
                }}
                QPushButton:focus {{ outline: none; border: none; }}
            """)
            self._labels[nav_id].setStyleSheet(
                f"font-size: 9px; background: transparent; border: none; color: {color};"
            )
    def _on_click(self, nav_id: str):
        if nav_id == self._current:
            return
        self._current = nav_id
        self._apply_styles()
        self.nav_changed.emit(nav_id)

    def select(self, nav_id: str):
        self._on_click(nav_id)
