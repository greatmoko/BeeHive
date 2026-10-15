"""TokBee 风格模型选择按钮：全项目统一的模型选择框（唯一入口）。

视觉：secondary 按钮 + 🤖 图标 + 当前模型名（宽度默认固定 300，可传
``width=None`` 改为随内容自适应）。
交互：点击弹出「厂商 / 模型」列表（当前项带 ✓），选择后发 model_selected。
数据：站点通过 ``set_options`` 注入可选列表（或传 ``options_provider`` 每次
点击时拉取），避免公共层反向依赖厂商存储。
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from beehive.common.ui.dialogs.compact import show_compact_tip
from beehive.common.ui.styles.buttons import apply_secondary_btn
from beehive.common.ui.styles.menus import make_context_menu
from beehive.common.ui.styles.metrics import DEFAULT_BTN_HEIGHT

ModelPair = tuple[str, str]
ModelOption = tuple[str, ModelPair]  # (显示名, (provider_id, model_id))


class ModelSelectorButton(QPushButton):
    """模型选择框：🤖 + 模型名按钮，点击弹出可选模型列表。"""

    model_selected = Signal(str, str)  # provider_id, model_id

    def __init__(
        self,
        theme,
        *,
        width: int | None = 300,
        placeholder: str = "未配置模型",
        hint_tooltip: str = "请选择模型",
        empty_tip: str = "请先在「AI配置 → 厂商设置」中配置并启用模型",
        options_provider: Callable[[], list[ModelOption]] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._theme = theme
        self._placeholder = placeholder
        self._hint_tooltip = hint_tooltip
        self._empty_tip = empty_tip
        self._options_provider = options_provider
        self._options: list[ModelOption] = []
        self._current: ModelPair = ("", "")
        self._transient: str | None = None  # 临时占位文案（如「未选择模型」）
        apply_secondary_btn(self, theme.colors)
        self.setStyleSheet(self.styleSheet() + """
            QPushButton { text-align: left; padding: 0 12px; }
        """)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if width is not None:
            self.setFixedWidth(width)
        self.setFixedHeight(DEFAULT_BTN_HEIGHT)
        self.clicked.connect(self.show_menu)
        self._render()

    # ── 数据注入 ──
    def set_options(self, options, current: ModelPair = ("", "")) -> None:
        """设置可选模型列表并选中 current；程序化设置不发信号。"""
        self._options = [
            (str(label), (str(p or ""), str(m or ""))) for label, (p, m) in options
        ]
        self._current = (str(current[0] or ""), str(current[1] or ""))
        self._transient = None
        self._render()

    def set_current(self, current) -> None:
        """选中模型（接受 (provider_id, model_id)）；只回显，不发信号。"""
        if current is None:
            return
        self._current = (str(current[0] or ""), str(current[1] or ""))
        self._transient = None
        self._render()

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        self._render()

    def show_placeholder(self, text: str) -> None:
        """临时占位文案（不改变默认 placeholder，选中后自动清除）。"""
        self._transient = text
        self._render()

    def set_options_provider(self, provider: Callable[[], list[ModelOption]]) -> None:
        """每次点击时动态拉取可选列表（模型列表随厂商设置变化时使用）。"""
        self._options_provider = provider

    # ── 取值 ──
    def current_model(self) -> ModelPair:
        return self._current

    def current_label(self) -> str:
        """当前模型显示名：优先取列表里的「厂商 / 模型」，找不到回退 model_id。"""
        if self._current[1]:
            for label, pair in self._options:
                if pair == self._current:
                    return label
            return self._current[1]
        return ""

    # ── 渲染与弹层 ──
    def _render(self):
        if self._transient:
            self.setText(self._transient)
            self.setToolTip(self._hint_tooltip)
        elif self._current[1]:
            self.setText(f"🤖 {self._current[1]}")
            self.setToolTip(f"正在使用：{self.current_label()}")
        else:
            self.setText(self._placeholder)
            self.setToolTip(self._hint_tooltip)

    def show_menu(self):
        if self._options_provider is not None:
            self._options = list(self._options_provider() or [])
        if not self._options:
            show_compact_tip(self.window(), self._theme, self._empty_tip)
            return
        c = self._theme.colors
        menu = make_context_menu(self, c)
        for label, pair in self._options:
            mark = " ✓" if pair == self._current else ""
            action = menu.addAction(f"{label}{mark}")
            action.setData(pair)
        chosen = menu.exec(self.mapToGlobal(self.rect().topLeft()))
        if chosen is not None:
            data = chosen.data()
            if isinstance(data, tuple) and len(data) == 2 and data[1]:
                self._current = (str(data[0]), str(data[1]))
                self._transient = None
                self._render()
                self.model_selected.emit(*self._current)
