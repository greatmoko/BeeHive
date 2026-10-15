"""模型行、筛选和模型设置弹窗，修改通过信号交给编辑控制器保存。"""
from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QScrollArea, QPushButton, QCheckBox
from beehive.common.ui.styles.inputs import apply_lineedit
from beehive.common.ui.styles.checks import apply_checkbox
from beehive.common.ui.styles.labels import style_hint_label
from beehive.system.providers.provider_models import ProviderModel
from beehive.system.providers.ui.provider_model_popup import _ModelSettingsPopup

class ProviderModelList(QWidget):
    options_changed = Signal()
    selection_changed = Signal()
    default_requested = Signal(str)
    delete_requested = Signal(str)


    def __init__(self, theme, store, parent=None):
        super().__init__(parent)
        self.theme, self.store = theme, store
        self._current_id = ""
        self._model_checks = []
        self._model_filter_empty = None
        self._model_popup = None
        c = theme.colors
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self._model_filter_edit = QLineEdit()
        self._model_filter_edit.setPlaceholderText("筛选模型（按名称或 ID）")
        self._model_filter_edit.setFixedHeight(34)
        apply_lineedit(self._model_filter_edit, c)
        self._model_filter_edit.setClearButtonEnabled(True)
        self._model_filter_edit.textChanged.connect(self._apply_model_filter)
        filter_row.addWidget(self._model_filter_edit, stretch=1)
        self._model_filter_hint = QLabel("")
        style_hint_label(self._model_filter_hint, c)
        self._model_filter_hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        filter_row.addWidget(self._model_filter_hint)
        layout.addLayout(filter_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self._model_container = QWidget()
        self._model_layout = QVBoxLayout(self._model_container)
        self._model_layout.setContentsMargins(0, 0, 0, 0)
        self._model_layout.setSpacing(4)
        self._model_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._model_container)
        layout.addWidget(scroll, stretch=1)

    def render(self, provider_id, models):
        self._current_id = provider_id
        self._render_models(models)

    def clear_filter(self):
        self._model_filter_edit.blockSignals(True)
        self._model_filter_edit.clear()
        self._model_filter_edit.blockSignals(False)

    def ensure_enabled(self, model_id):
        for cb, model, _row in self._model_checks:
            if model.model_id == model_id:
                cb.setChecked(True)
                break

    def _model_filter_query(self) -> str:
        if not hasattr(self, "_model_filter_edit"):
            return ""
        return self._model_filter_edit.text().strip().lower()

    def _model_matches_filter(self, model: ProviderModel, query: str) -> bool:
        if not query:
            return True
        haystack = f"{model.nickname or ''} {model.model_id}".lower()
        return query in haystack

    def _apply_model_filter(self, *_args):
        query = self._model_filter_query()
        total = len(self._model_checks)
        visible = 0
        for _cb, m, row in self._model_checks:
            show = self._model_matches_filter(m, query)
            row.setVisible(show)
            if show:
                visible += 1
        if self._model_filter_empty is not None:
            self._model_filter_empty.setVisible(total > 0 and query != "" and visible == 0)
        if query and total:
            self._model_filter_hint.setText(f"显示 {visible} / {total}")
        else:
            self._model_filter_hint.setText(f"共 {total} 个" if total else "")

    def _render_models(self, models: list[ProviderModel]):
        self._close_model_popup()
        while self._model_layout.count():
            item = self._model_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._model_checks.clear()
        self._model_filter_empty = None
        c = self.theme.colors
        if not models:
            empty = QLabel("暂无模型 — 请「拉取远程模型」或「+ 添加」")
            empty.setStyleSheet(f"color: {c['text_hint']}; font-size: 12px;")
            self._model_layout.addWidget(empty)
            self._apply_model_filter()
            return
        for m in models:
            row = QWidget()
            row_l = QHBoxLayout(row)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(8)

            cb = QCheckBox(m.nickname)
            cb.setChecked(m.enabled)
            cb.setToolTip(m.model_id if m.nickname else "")
            apply_checkbox(cb, c)
            cb.toggled.connect(self.selection_changed)
            row_l.addWidget(cb)

            gear = QPushButton("⚙")
            gear.setToolTip("模型设置")
            gear.setCursor(Qt.CursorShape.PointingHandCursor)
            gear.setFixedSize(28, 26)
            gear.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {c["text_secondary"]};
                    border: 1px solid {c["border"]}; border-radius: 4px;
                    font-size: 13px;
                }}
                QPushButton:hover {{ background: {c["subnav_hover"]}; color: {c["accent"]}; }}
            """)
            gear.clicked.connect(
                lambda _=False, model=m, btn=gear: self._open_model_settings(model, btn)
            )
            row_l.addWidget(gear)

            mid_lbl = QLabel(m.model_id)
            mid_lbl.setToolTip(m.nickname if m.nickname else "")
            mid_lbl.setStyleSheet(
                f"font-size: 12px; color: {c['text_secondary']};"
            )
            row_l.addWidget(mid_lbl)

            is_default = self.store.is_default_model(self._current_id, m.model_id)
            if is_default:
                badge = QLabel("默认")
                badge.setStyleSheet(f"""
                    QLabel {{
                        color: {c["accent"]}; font-size: 11px; font-weight: 600;
                        padding: 2px 8px; border: 1px solid {c["border"]};
                        border-radius: 4px;
                    }}
                """)
                row_l.addWidget(badge)

            row_l.addStretch(1)
            self._model_layout.addWidget(row)
            self._model_checks.append((cb, m, row))

        self._model_filter_empty = QLabel("无匹配模型，请调整筛选关键词")
        style_hint_label(self._model_filter_empty, c)
        self._model_filter_empty.setVisible(False)
        self._model_layout.addWidget(self._model_filter_empty)
        self._apply_model_filter()

    def _close_model_popup(self):
        if self._model_popup is not None:
            self._model_popup.close()
            self._model_popup.deleteLater()
            self._model_popup = None

    def _open_model_settings(self, model: ProviderModel, anchor: QWidget):
        self._close_model_popup()
        is_default = self.store.is_default_model(self._current_id, model.model_id)
        popup = _ModelSettingsPopup(
            self.theme, model, family=self.store.get_family(self._current_id),
            is_default=is_default, parent=self.window(),
        )
        popup.context_changed.connect(self._on_model_context_changed)
        popup.protocol_changed.connect(self._on_model_protocol_changed)
        popup.options_changed.connect(self._on_model_options_changed)
        popup.set_default.connect(self.default_requested)
        popup.delete_model.connect(self.delete_requested)
        self._model_popup = popup
        # 锚点右下角外侧弹出
        pos = anchor.mapToGlobal(QPoint(0, anchor.height() + 2))
        popup.popup_at(pos)

    def _on_model_context_changed(self, model_id: str, context_window: int):
        for _cb, m, _row in self._model_checks:
            if m.model_id == model_id:
                m.context_window = int(context_window)
                break
        self.options_changed.emit()

    def _on_model_protocol_changed(self, model_id: str, protocol: str):
        for _cb, m, _row in self._model_checks:
            if m.model_id == model_id:
                m.api_protocol = protocol if protocol in ("chat", "responses") else "chat"
                break
        self.options_changed.emit()

    def _on_model_options_changed(self, model_id: str, options: dict):
        for _cb, m, _row in self._model_checks:
            if m.model_id == model_id:
                for key, value in options.items():
                    setattr(m, key, value)
                break
        self.options_changed.emit()

    def collect_models(self) -> list[ProviderModel]:
        models: list[ProviderModel] = []
        for cb, m, _row in self._model_checks:
            models.append(ProviderModel(
                model_id=m.model_id,
                nickname=m.nickname,
                capabilities=list(m.capabilities),
                context_window=int(m.context_window or 0),
                max_output=m.max_output,
                enabled=cb.isChecked(),
                api_protocol=m.api_protocol,
                temperature=m.temperature,
                top_p=m.top_p,
                max_tokens=m.max_tokens,
                stream=m.stream,
                reasoning_enabled=m.reasoning_enabled,
                reasoning_adapter=m.reasoning_adapter,
                reasoning_effort=m.reasoning_effort,
            ))
        return models
