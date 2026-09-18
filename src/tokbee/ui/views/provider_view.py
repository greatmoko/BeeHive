"""厂商设置工作区 — 我的厂商可自选添加；Ollama 与自定义本地分离。"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal, QThread, QTimer, QPoint
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QFrame,
    QLabel, QPushButton, QScrollArea, QLineEdit,
    QDialog, QCheckBox, QListWidget, QListWidgetItem,
    QInputDialog, QComboBox, QStackedWidget, QSpinBox, QDoubleSpinBox,
    QRadioButton, QButtonGroup, QApplication,
)

from tokbee.ui.styles.theme import Theme
from ui_common.dialogs import show_tip
from tokbee.ui.styles.system import (
    apply_checkbox, apply_combo_popup_style, apply_danger_btn, apply_lineedit, apply_radio,
    apply_secondary_btn, apply_spin, section_label_qss, style_hint_label,
)
from tokbee.core.provider_store import ProviderStore, ProviderSettings, ProviderModel
from tokbee.core.provider import get_builtin
from tokbee.core.errors import AIError
from tokbee.core.request_builder import OPENAI_EFFORT_VALUES

logger = logging.getLogger("tokbee")

from tokbee.ui.views.provider_dialogs import (
    _tip,
    _confirm,
    _FetchModelsWorker,
    _AddProviderDialog,
)
from tokbee.ui.views.provider_model_popup import _ModelSettingsPopup

# 添加弹窗中「自定义本地 API」的特殊选项 id
class ProviderSettingsWorkspace(QWidget):
    """厂商设置：左为我的厂商列表，右为详情。"""

    def __init__(self, theme: Theme, store: ProviderStore | None = None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.store = store or ProviderStore()
        self._current_id = ""
        self._model_checks: list = []  # (QCheckBox, ProviderModel, row_widget)
        self._model_filter_empty: QLabel | None = None
        self._model_popup: _ModelSettingsPopup | None = None
        self._fetch_worker: _FetchModelsWorker | None = None
        self._loading = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(400)
        self._autosave_timer.timeout.connect(self._autosave_now)
        self._build()
        self._show_empty_detail()

    def _build(self):
        c = self.theme.colors
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        left = QFrame()
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
        add_btn.clicked.connect(self._on_add_provider)
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
        self._list.currentItemChanged.connect(self._on_list_changed)
        left_l.addWidget(self._list, stretch=1)

        empty_hint = QLabel("点击右上角 + 添加厂商")
        empty_hint.setWordWrap(True)
        empty_hint.setStyleSheet(f"font-size: 11px; color: {c['text_hint']}; padding: 4px 8px;")
        self._left_empty = empty_hint
        left_l.addWidget(empty_hint)

        root.addWidget(left)

        self._right_stack = QStackedWidget()
        # page 0: empty
        empty_page = QWidget()
        el = QVBoxLayout(empty_page)
        el.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_msg = QLabel("尚未选择厂商\n请从左侧添加或选择一个厂商进行配置")
        empty_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_msg.setStyleSheet(f"font-size: 14px; color: {c['text_hint']};")
        el.addWidget(empty_msg)
        self._empty_default_lbl = QLabel("")
        self._empty_default_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_default_lbl.setStyleSheet(
            f"font-size: 12px; color: {c['text_secondary']}; margin-top: 12px;"
        )
        el.addWidget(self._empty_default_lbl)
        self._right_stack.addWidget(empty_page)

        # page 1: detail
        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(28, 24, 28, 24)
        right_l.setSpacing(12)

        self._title = QLabel("厂商设置")
        self._title.setStyleSheet(f"font-size: 20px; font-weight: bold; color: {c['text']};")
        right_l.addWidget(self._title)

        self._notes = QLabel("")
        self._notes.setWordWrap(True)
        self._notes.setStyleSheet(f"font-size: 12px; color: {c['text_hint']};")
        right_l.addWidget(self._notes)

        self._default_hint = QLabel("")
        self._default_hint.setWordWrap(True)
        self._default_hint.setStyleSheet(
            f"font-size: 12px; color: {c['text_secondary']};"
        )
        right_l.addWidget(self._default_hint)

        lbl_style = f"font-size: 13px; font-weight: 600; color: {c['text']};"
        inp_style = f"""
            QLineEdit {{
                background: {c["input_bg"]}; border: 1px solid {c["input_border"]};
                border-radius: 6px; padding: 0 10px; color: {c["text"]}; font-size: 13px;
            }}
            QLineEdit:focus {{ border-color: {c["input_focus_border"]}; }}
        """

        key_lbl = QLabel("API Key")
        key_lbl.setStyleSheet(lbl_style)
        right_l.addWidget(key_lbl)
        self._key_edit = QLineEdit()
        self._key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_edit.setFixedHeight(36)
        self._key_edit.setPlaceholderText("输入 API Key（本地可留空）")
        self._key_edit.setStyleSheet(inp_style)
        self._key_edit.textChanged.connect(self._schedule_autosave)
        self._key_edit.editingFinished.connect(self._autosave_now)
        right_l.addWidget(self._key_edit)

        host_lbl = QLabel("API Host")
        host_lbl.setStyleSheet(lbl_style)
        right_l.addWidget(host_lbl)
        self._host_edit = QLineEdit()
        self._host_edit.setFixedHeight(36)
        self._host_edit.setPlaceholderText("https://api.example.com/v1")
        self._host_edit.setStyleSheet(inp_style)
        self._host_edit.textChanged.connect(self._schedule_autosave)
        self._host_edit.editingFinished.connect(self._autosave_now)
        right_l.addWidget(self._host_edit)

        model_row = QHBoxLayout()
        model_lbl = QLabel("模型列表")
        model_lbl.setStyleSheet(lbl_style)
        model_row.addWidget(model_lbl)
        model_row.addStretch()

        self._query_btn = QPushButton("拉取远程模型")
        self._query_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._query_btn.setFixedHeight(30)
        self._query_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_bg"]}; color: {c["text"]};
                border: none; border-radius: 6px; padding: 0 12px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {c["btn_hover"]}; }}
        """)
        self._query_btn.clicked.connect(self._on_fetch_models)
        model_row.addWidget(self._query_btn)

        add_model_btn = QPushButton("+ 添加")
        add_model_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_model_btn.setFixedHeight(30)
        add_model_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_bg"]}; color: {c["accent"]};
                border: none; border-radius: 6px; padding: 0 12px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {c["btn_hover"]}; }}
        """)
        add_model_btn.clicked.connect(self._on_add_model)
        model_row.addWidget(add_model_btn)

        clear_models_btn = QPushButton("清空模型 ID")
        clear_models_btn.setToolTip("一键清空当前厂商的全部模型 ID")
        apply_danger_btn(clear_models_btn, c, height=30)
        clear_models_btn.clicked.connect(self._on_clear_models)
        model_row.addWidget(clear_models_btn)
        right_l.addLayout(model_row)

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
        right_l.addLayout(filter_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self._model_container = QWidget()
        self._model_layout = QVBoxLayout(self._model_container)
        self._model_layout.setContentsMargins(0, 0, 0, 0)
        self._model_layout.setSpacing(4)
        self._model_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._model_container)
        right_l.addWidget(scroll, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        reset_btn = QPushButton("恢复默认 Host")
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.setFixedHeight(34)
        reset_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {c["text_secondary"]};
                border: 1px solid {c["border"]}; border-radius: 6px; padding: 0 14px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {c["subnav_hover"]}; }}
        """)
        reset_btn.clicked.connect(self._on_reset)
        self._reset_btn = reset_btn
        btn_row.addWidget(reset_btn)

        del_btn = QPushButton("从列表移除")
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setFixedHeight(34)
        del_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {c["danger"]};
                border: 1px solid {c["border"]}; border-radius: 6px; padding: 0 14px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {c["subnav_hover"]}; }}
        """)
        del_btn.clicked.connect(self._on_remove)
        self._del_btn = del_btn
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        right_l.addLayout(btn_row)

        self._right_stack.addWidget(right)
        root.addWidget(self._right_stack, stretch=1)
        self._reload_list()

    def showEvent(self, event):
        super().showEvent(event)
        self._reload_list(keep_current=True)

    def _show_empty_detail(self):
        self._autosave_now()
        self._current_id = ""
        self._list.clearSelection()
        self._right_stack.setCurrentIndex(0)
        self._refresh_default_labels()

    def _refresh_default_labels(self):
        label = f"默认模型：{self.store.default_display_label()}"
        if hasattr(self, "_empty_default_lbl"):
            self._empty_default_lbl.setText(label)
        if hasattr(self, "_default_hint"):
            self._default_hint.setText(label)

    def _reload_list(self, keep_current: bool = False):
        cur = self._current_id if keep_current else ""
        self._list.blockSignals(True)
        self._list.clear()
        for pid, name, icon, _family in self.store.list_my_providers():
            item = QListWidgetItem(f"{icon}  {name}")
            item.setData(Qt.ItemDataRole.UserRole, pid)
            self._list.addItem(item)
        self._list.blockSignals(False)
        self._left_empty.setVisible(self._list.count() == 0)

        if cur:
            for i in range(self._list.count()):
                if self._list.item(i).data(Qt.ItemDataRole.UserRole) == cur:
                    self._list.setCurrentRow(i)
                    self._refresh_default_labels()
                    return
        # 默认不选中任何厂商
        self._list.clearSelection()
        if not cur or cur not in {self._list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self._list.count())}:
            self._show_empty_detail()
        else:
            self._refresh_default_labels()

    def _on_list_changed(self, current: QListWidgetItem | None, _prev):
        if not current:
            self._show_empty_detail()
            return
        self._select_provider(current.data(Qt.ItemDataRole.UserRole))

    def _select_provider(self, provider_id: str):
        if self._current_id and self._current_id != provider_id:
            self._autosave_now()
        self._autosave_timer.stop()
        self._loading = True
        self._current_id = provider_id
        self._model_filter_edit.blockSignals(True)
        self._model_filter_edit.clear()
        self._model_filter_edit.blockSignals(False)
        self._right_stack.setCurrentIndex(1)
        name = self.store.get_display_name(provider_id)
        self._title.setText(name)
        builtin = get_builtin(provider_id)
        if builtin:
            self._notes.setText(builtin.notes)
            self._reset_btn.setVisible(True)
        else:
            self._notes.setText("自定义 OpenAI 兼容本地 / 私有 API")
            self._reset_btn.setVisible(False)

        settings = self.store.get_settings(provider_id)
        self._key_edit.setText(settings.api_key)
        self._host_edit.setText(settings.api_host)
        self._render_models(settings.models)
        self._refresh_default_labels()
        self._loading = False

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
            cb.toggled.connect(self._on_model_toggled)
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
        popup.set_default.connect(self._on_set_default)
        popup.delete_model.connect(self._on_delete_model)
        self._model_popup = popup
        # 锚点右下角外侧弹出
        pos = anchor.mapToGlobal(QPoint(0, anchor.height() + 2))
        popup.popup_at(pos)

    def _on_model_context_changed(self, model_id: str, context_window: int):
        for _cb, m, _row in self._model_checks:
            if m.model_id == model_id:
                m.context_window = int(context_window)
                break
        self._schedule_autosave()

    def _on_model_protocol_changed(self, model_id: str, protocol: str):
        for _cb, m, _row in self._model_checks:
            if m.model_id == model_id:
                m.api_protocol = protocol if protocol in ("chat", "responses") else "chat"
                break
        self._schedule_autosave()

    def _on_model_options_changed(self, model_id: str, options: dict):
        for _cb, m, _row in self._model_checks:
            if m.model_id == model_id:
                for key, value in options.items():
                    setattr(m, key, value)
                break
        self._schedule_autosave()

    def _schedule_autosave(self, *_args):
        if self._loading or not self._current_id:
            return
        self._autosave_timer.start()

    def _autosave_now(self, *_args):
        if self._loading or not self._current_id:
            return
        self._autosave_timer.stop()
        self.store.update_settings(self._current_id, self._collect_settings())
        self._refresh_default_labels()

    def _on_model_toggled(self, *_args):
        self._autosave_now()

    def _on_delete_model(self, model_id: str):
        if not self._current_id or not model_id:
            return
        self._close_model_popup()
        if not _confirm(self, self.theme, f"确定删除模型「{model_id}」？"):
            return
        settings = self._collect_settings()
        settings.models = [m for m in settings.models if m.model_id != model_id]
        self.store.update_settings(self._current_id, settings)
        if self.store.is_default_model(self._current_id, model_id):
            self.store.clear_default_model()
        self._render_models(settings.models)
        self._refresh_default_labels()

    def _on_set_default(self, model_id: str):
        if not self._current_id or not model_id:
            return
        self._close_model_popup()
        for cb, m, _row in self._model_checks:
            if m.model_id == model_id:
                cb.setChecked(True)
                break
        settings = self._collect_settings()
        if not (settings.api_host or "").strip():
            _tip(self, self.theme, "请先填写 API Host，再设为默认模型")
            return
        self.store.update_settings(self._current_id, settings)
        try:
            self.store.set_default_model(self._current_id, model_id)
        except ValueError as e:
            _tip(self, self.theme, str(e))
            return
        self._render_models(self.store.get_settings(self._current_id).models)
        self._refresh_default_labels()

    def _collect_settings(self) -> ProviderSettings:
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
        return ProviderSettings(
            api_key=self._key_edit.text().strip(),
            api_host=self._host_edit.text().strip(),
            models=models,
        )

    def _on_add_provider(self):
        dlg = _AddProviderDialog(self.theme, self.store, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if dlg.result_builtin_id:
            ok = self.store.add_builtin_provider(dlg.result_builtin_id)
            if not ok:
                _tip(self, self.theme, "该厂商已在列表中")
                return
            new_id = dlg.result_builtin_id
        elif dlg.result_custom:
            name, host, key = dlg.result_custom
            try:
                info = self.store.add_custom_provider(name, host, key)
            except ValueError as e:
                _tip(self, self.theme, str(e))
                return
            new_id = info.id
        else:
            return

        self._reload_list()
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole) == new_id:
                self._list.setCurrentRow(i)
                break

    def _on_reset(self):
        if not self._current_id or self.store.is_custom(self._current_id):
            return
        self._autosave_timer.stop()
        self.store.reset_provider(self._current_id, keep_api_key=True)
        self._select_provider(self._current_id)
        _tip(self, self.theme, "已恢复默认 Host（保留 API Key；模型列表已清空，请重新拉取或添加）")

    def _on_remove(self):
        if not self._current_id:
            return
        self._autosave_timer.stop()
        self.store.remove_from_my_list(self._current_id)
        self._current_id = ""
        self._reload_list()
        self._show_empty_detail()

    def _on_clear_models(self):
        if not self._current_id:
            return
        settings = self._collect_settings()
        n = len(settings.models)
        if n == 0:
            _tip(self, self.theme, "当前没有可清空的模型 ID")
            return
        if not _confirm(
            self,
            self.theme,
            f"确定清空当前厂商的全部 {n} 个模型 ID？可稍后重新拉取或手动添加。",
            title="清空模型 ID",
        ):
            return
        self._close_model_popup()
        self._autosave_timer.stop()
        settings.models = []
        self.store.update_settings(self._current_id, settings)
        self._render_models([])
        self._refresh_default_labels()
        _tip(self, self.theme, f"已清空 {n} 个模型 ID")

    def _on_add_model(self):
        if not self._current_id:
            return
        mid, ok = QInputDialog.getText(self, "添加模型", "模型 ID：")
        if not ok or not mid.strip():
            return
        settings = self._collect_settings()
        if any(m.model_id == mid.strip() for m in settings.models):
            _tip(self, self.theme, "模型已存在")
            return
        settings.models.append(ProviderModel(model_id=mid.strip(), enabled=False))
        self.store.update_settings(self._current_id, settings)
        self._render_models(settings.models)

    def _on_fetch_models(self):
        if not self._current_id:
            return
        host = self._host_edit.text().strip()
        if not host:
            _tip(self, self.theme, "请先填写 API Host")
            return
        self._autosave_now()
        self._query_btn.setEnabled(False)
        self._query_btn.setText("拉取中…")
        self._fetch_worker = _FetchModelsWorker(host, self._key_edit.text().strip(), self)
        self._fetch_worker.finished_ok.connect(self._on_fetch_ok)
        self._fetch_worker.finished_err.connect(self._on_fetch_err)
        self._fetch_worker.start()

    def _on_fetch_ok(self, model_ids: list):
        self._query_btn.setEnabled(True)
        self._query_btn.setText("拉取远程模型")
        settings = self._collect_settings()
        existing = {m.model_id for m in settings.models}
        for mid in model_ids:
            if mid not in existing:
                settings.models.append(ProviderModel(model_id=mid, enabled=False))
        self.store.update_settings(self._current_id, settings)
        self._render_models(settings.models)
        _tip(self, self.theme, f"已获取 {len(model_ids)} 个模型，请勾选需要启用的项")

    def _on_fetch_err(self, err: str):
        self._query_btn.setEnabled(True)
        self._query_btn.setText("拉取远程模型")
        _tip(self, self.theme, f"拉取失败：{err}")
