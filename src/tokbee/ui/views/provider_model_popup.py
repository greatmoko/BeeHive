"""模型参数弹窗."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QCheckBox, QComboBox, QSpinBox, QDoubleSpinBox, QRadioButton, QButtonGroup, QApplication

from tokbee.ui.styles.theme import Theme
from tokbee.ui.styles.system import apply_checkbox, apply_combo_popup_style, apply_danger_btn, apply_radio, apply_secondary_btn, apply_spin, section_label_qss, style_hint_label
from tokbee.core.provider_store import ProviderModel
from tokbee.core.request_builder import OPENAI_EFFORT_VALUES

OPENAI_EFFORT_CHOICES = [("默认（不发送）", "")] + [(v, v) for v in OPENAI_EFFORT_VALUES]
class _ModelSettingsPopup(QFrame):
    """模型级调用参数与连接设置浮层。"""

    context_changed = Signal(str, int)   # model_id, context_window
    protocol_changed = Signal(str, str)  # model_id, chat | responses
    options_changed = Signal(str, object)
    set_default = Signal(str)
    delete_model = Signal(str)

    def __init__(
        self,
        theme: Theme,
        model: ProviderModel,
        *,
        family: str = "",
        is_default: bool,
        parent=None,
    ):
        super().__init__(parent, Qt.WindowType.Popup)
        self.theme = theme
        self._model_id = model.model_id
        self.family = family
        c = theme.colors
        self.setObjectName("modelSettingsPopup")
        self.setStyleSheet(f"""
            QFrame#modelSettingsPopup {{
                background: {c["content_bg"]};
                border: 1px solid {c["border"]};
                border-radius: 8px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        title = QLabel(model.nickname or model.model_id)
        title.setStyleSheet(section_label_qss(c))
        title.setWordWrap(True)
        layout.addWidget(title)

        ctx_row = QHBoxLayout()
        ctx_row.setSpacing(8)
        ctx_lbl = QLabel("上下文窗口")
        ctx_lbl.setStyleSheet(section_label_qss(c))
        ctx_row.addWidget(ctx_lbl)
        self._ctx_spin = QSpinBox()
        self._ctx_spin.setRange(0, 10_000_000)
        self._ctx_spin.setSingleStep(1024)
        self._ctx_spin.setValue(int(model.context_window or 0))
        self._ctx_spin.setToolTip("0 表示未设置 tokens")
        self._ctx_spin.setMinimumWidth(120)
        apply_spin(self._ctx_spin, c)
        self._ctx_spin.valueChanged.connect(self._on_ctx)
        ctx_row.addWidget(self._ctx_spin, stretch=1)
        layout.addLayout(ctx_row)

        hint = QLabel("单位 tokens")
        style_hint_label(hint, c)
        layout.addWidget(hint)

        self._temperature = QDoubleSpinBox()
        self._temperature.setRange(0.0, 2.0)
        self._temperature.setDecimals(2)
        self._temperature.setSingleStep(0.1)
        self._temperature.setValue(0.7 if model.temperature is None else model.temperature)
        self._temperature.setEnabled(model.temperature is not None)
        self._top_p = QDoubleSpinBox()
        self._top_p.setRange(0.0, 1.0)
        self._top_p.setDecimals(2)
        self._top_p.setSingleStep(0.05)
        self._top_p.setValue(1.0 if model.top_p is None else model.top_p)
        self._top_p.setEnabled(model.top_p is not None)
        self._max_tokens = QSpinBox()
        self._max_tokens.setRange(1, 256000)
        self._max_tokens.setSingleStep(256)
        self._max_tokens.setValue(model.max_tokens or 8192)
        self._max_tokens.setEnabled(model.max_tokens is not None)
        for label, widget in (("Temperature", self._temperature), ("Top P", self._top_p), ("Max Tokens", self._max_tokens)):
            row = QHBoxLayout()
            title_lbl = QLabel(label)
            title_lbl.setStyleSheet(section_label_qss(c))
            row.addWidget(title_lbl)
            row.addStretch()
            row.addWidget(widget)
            layout.addLayout(row)
        self._temperature_enable = QCheckBox("发送 Temperature")
        self._temperature_enable.setChecked(model.temperature is not None)
        self._temperature_enable.toggled.connect(self._temperature.setEnabled)
        self._temperature_enable.toggled.connect(self._emit_options)
        self._top_p_enable = QCheckBox("发送 Top P")
        self._top_p_enable.setChecked(model.top_p is not None)
        self._top_p_enable.toggled.connect(self._top_p.setEnabled)
        self._top_p_enable.toggled.connect(self._emit_options)
        self._max_tokens_enable = QCheckBox("发送 Max Tokens")
        self._max_tokens_enable.setChecked(model.max_tokens is not None)
        self._max_tokens_enable.toggled.connect(self._max_tokens.setEnabled)
        self._max_tokens_enable.toggled.connect(self._emit_options)
        for check in (self._temperature_enable, self._top_p_enable, self._max_tokens_enable):
            apply_checkbox(check, c)
            layout.addWidget(check)
        self._stream = QCheckBox("启用流式输出")
        self._stream.setChecked(model.stream)
        self._stream.toggled.connect(self._emit_options)
        apply_checkbox(self._stream, c)
        layout.addWidget(self._stream)

        self._reasoning = QCheckBox("启用推理")
        self._reasoning.setChecked(model.reasoning_enabled)
        self._reasoning.toggled.connect(self._emit_options)
        apply_checkbox(self._reasoning, c)
        layout.addWidget(self._reasoning)

        ctrl_section = QLabel("推理适配（请按具体模型选择生效机制）")
        ctrl_section.setStyleSheet(section_label_qss(c))
        ctrl_section.setWordWrap(True)
        layout.addWidget(ctrl_section)
        self._adapter_group = QButtonGroup(self)
        self._adapter_group.setExclusive(True)
        adapter_choices = (
            ("openai", "OpenAI 风格（顶层 reasoning_effort）"),
            ("deepseek", "DeepSeek 风格（extra_body thinking + reasoning_effort）"),
        )
        for key, label in adapter_choices:
            rb = QRadioButton(label)
            rb.setProperty("value", key)
            self._adapter_group.addButton(rb)
            rb.setChecked((model.reasoning_adapter or "openai") == key)
            rb.toggled.connect(lambda _on, k=key: self._on_adapter_changed(k))
            apply_radio(rb, c)
            layout.addWidget(rb)

        effort_row = QHBoxLayout()
        effort_lbl = QLabel("推理强度")
        effort_lbl.setStyleSheet(section_label_qss(c))
        effort_row.addWidget(effort_lbl)
        self._effort_combo = QComboBox()
        self._effort_combo.setMinimumWidth(140)
        apply_combo_popup_style(self._effort_combo, c)
        self._effort_combo.currentIndexChanged.connect(self._emit_options)
        effort_row.addWidget(self._effort_combo)
        layout.addLayout(effort_row)
        self._fill_effort_combo((model.reasoning_adapter or "openai"), model.reasoning_effort)

        for widget in (self._temperature, self._top_p, self._max_tokens):
            widget.valueChanged.connect(self._emit_options)

        protocol_row = QHBoxLayout()
        protocol_row.setSpacing(8)
        protocol_lbl = QLabel("API 协议")
        protocol_lbl.setStyleSheet(section_label_qss(c))
        protocol_row.addWidget(protocol_lbl)
        self._protocol_combo = QComboBox()
        self._protocol_combo.addItem("Chat Completions（默认）", "chat")
        self._protocol_combo.addItem("Responses API", "responses")
        index = self._protocol_combo.findData(model.api_protocol or "chat")
        self._protocol_combo.setCurrentIndex(max(0, index))
        self._protocol_combo.setMinimumWidth(120)
        apply_combo_popup_style(self._protocol_combo, c)
        self._protocol_combo.currentIndexChanged.connect(
            lambda _i: self.protocol_changed.emit(
                self._model_id, str(self._protocol_combo.currentData() or "chat")
            )
        )
        protocol_row.addWidget(self._protocol_combo, stretch=1)
        layout.addLayout(protocol_row)

        if is_default:
            def_btn = QPushButton("✓ 当前为默认模型")
            def_btn.setEnabled(False)
        else:
            def_btn = QPushButton("设为默认模型")
            def_btn.clicked.connect(self._on_default)
        apply_secondary_btn(def_btn, c, height=32)
        layout.addWidget(def_btn)

        del_btn = QPushButton("删除此模型")
        apply_danger_btn(del_btn, c, height=32)
        del_btn.clicked.connect(self._on_delete)
        layout.addWidget(del_btn)

        self.setFixedWidth(300)
        self.adjustSize()

    def _on_ctx(self, value: int):
        self.context_changed.emit(self._model_id, int(value))

    def _emit_options(self, *_args):
        self.options_changed.emit(self._model_id, {
            "temperature": self._temperature.value() if self._temperature_enable.isChecked() else None,
            "top_p": self._top_p.value() if self._top_p_enable.isChecked() else None,
            "max_tokens": self._max_tokens.value() if self._max_tokens_enable.isChecked() else None,
            "stream": self._stream.isChecked(),
            "reasoning_enabled": self._reasoning.isChecked(),
            "reasoning_adapter": (btn.property("value") if (btn := self._adapter_group.checkedButton()) else "openai"),
            "reasoning_effort": self._effort_combo.currentData() or "",
        })

    def _adapter_choices(self, adapter: str) -> list[tuple[str, str]]:
        if adapter == "deepseek":
            return [("默认（不发送）", ""), ("低 low", "low"), ("高 high", "high"), ("最大 max", "max")]
        return OPENAI_EFFORT_CHOICES

    def _fill_effort_combo(self, adapter: str, current: str):
        self._effort_combo.blockSignals(True)
        self._effort_combo.clear()
        for text, value in self._adapter_choices(adapter):
            self._effort_combo.addItem(text, value)
        self._effort_combo.setCurrentIndex(max(0, self._effort_combo.findData(current or "")))
        self._effort_combo.blockSignals(False)

    def _on_adapter_changed(self, adapter: str):
        self._fill_effort_combo(adapter, "")
        self._emit_options()

    def _on_default(self):
        mid = self._model_id
        self.close()
        self.set_default.emit(mid)

    def _on_delete(self):
        mid = self._model_id
        self.close()
        self.delete_model.emit(mid)

    def popup_at(self, global_pos: QPoint):
        """在按钮附近弹出，必要时向左/上收拢以免出屏。"""
        self.adjustSize()
        screen = QApplication.screenAt(global_pos) or QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else None
        x, y = global_pos.x(), global_pos.y()
        if geo is not None:
            if x + self.width() > geo.right():
                x = geo.right() - self.width() - 4
            if y + self.height() > geo.bottom():
                y = global_pos.y() - self.height() - 4
            x = max(geo.left() + 4, x)
            y = max(geo.top() + 4, y)
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()



__all__ = ["_ModelSettingsPopup"]
