"""厂商编辑流程：选择、自动保存及用户发起的配置操作。"""
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QDialog
from beehive.common.ui.dialogs.message import _confirm
from beehive.common.ui.dialogs.text_input import _ask_text
from beehive.system.providers.provider import get_builtin
from beehive.system.providers.provider_models import ProviderModel
from beehive.system.providers.ui.provider_dialogs import _tip, _AddProviderDialog
from beehive.system.providers.ui.provider_fetch import ProviderFetchController

class ProviderEditorController(QObject):


    def __init__(self, theme, store, sidebar, detail, parent):
        super().__init__(parent)
        self.theme, self.store = theme, store
        self.sidebar, self.detail = sidebar, detail
        self._current_id = ""
        self._loading = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(400)
        self._autosave_timer.timeout.connect(self._autosave_now)
        self.fetch = ProviderFetchController(self)
        sidebar.provider_selected.connect(self.select_provider)
        sidebar.add_requested.connect(self._on_add_provider)
        detail.changed.connect(self._schedule_autosave)
        detail.editing_finished.connect(self._autosave_now)
        detail.fetch_requested.connect(self.fetch.start)
        detail.add_model_requested.connect(self._on_add_model)
        detail.clear_models_requested.connect(self._on_clear_models)
        detail.reset_requested.connect(self._on_reset)
        detail.remove_requested.connect(self._on_remove)
        detail.models.options_changed.connect(self._schedule_autosave)
        detail.models.selection_changed.connect(self._autosave_now)
        detail.models.default_requested.connect(self._on_set_default)
        detail.models.delete_requested.connect(self._on_delete_model)
        self._reload_list()
        self._show_empty_detail()

    def select_provider(self, provider_id):
        if provider_id:
            self._select_provider(provider_id)
        else:
            self._show_empty_detail()

    def _show_empty_detail(self):
        self._autosave_now()
        self._current_id = ""
        self.sidebar.clear_selection()
        self.detail._right_stack.setCurrentIndex(0)
        self._refresh_default_labels()

    def _refresh_default_labels(self):
        label = f"默认模型：{self.store.default_display_label()}"
        self.detail._empty_default_lbl.setText(label)
        self.detail._default_hint.setText(label)

    def _reload_list(self, keep_current=False):
        current = self._current_id if keep_current else ""
        if not self.sidebar.render(self.store.list_my_providers(), current):
            self._show_empty_detail()
        else:
            self._refresh_default_labels()

    def _select_provider(self, provider_id: str):
        if self._current_id and self._current_id != provider_id:
            self._autosave_now()
        self._autosave_timer.stop()
        self._loading = True
        self._current_id = provider_id
        self.detail.models.clear_filter()
        self.detail._right_stack.setCurrentIndex(1)
        name = self.store.get_display_name(provider_id)
        self.detail._title.setText(name)
        builtin = get_builtin(provider_id)
        if builtin:
            self.detail._notes.setText(builtin.notes)
            self.detail._reset_btn.setVisible(True)
        else:
            self.detail._notes.setText("自定义 OpenAI 兼容本地 / 私有 API")
            self.detail._reset_btn.setVisible(False)

        settings = self.store.get_settings(provider_id)
        self.detail._key_edit.setText(settings.api_key)
        self.detail._host_edit.setText(settings.api_host)
        self.detail.models.render(self._current_id, settings.models)
        self._refresh_default_labels()
        self._loading = False

    def _schedule_autosave(self, *_args):
        if self._loading or not self._current_id:
            return
        self._autosave_timer.start()

    def _autosave_now(self, *_args):
        if self._loading or not self._current_id:
            return
        self._autosave_timer.stop()
        self.store.update_settings(self._current_id, self.detail.collect_settings())
        self._refresh_default_labels()

    def _on_delete_model(self, model_id: str):
        if not self._current_id or not model_id:
            return
        self.detail.models._close_model_popup()
        if not _confirm(self.parent(), self.theme, "删除模型", f"确定删除模型「{model_id}」？"):
            return
        settings = self.detail.collect_settings()
        settings.models = [m for m in settings.models if m.model_id != model_id]
        self.store.update_settings(self._current_id, settings)
        if self.store.is_default_model(self._current_id, model_id):
            self.store.clear_default_model()
        self.detail.models.render(self._current_id, settings.models)
        self._refresh_default_labels()

    def _on_set_default(self, model_id: str):
        if not self._current_id or not model_id:
            return
        self.detail.models._close_model_popup()
        self.detail.models.ensure_enabled(model_id)
        settings = self.detail.collect_settings()
        if not (settings.api_host or "").strip():
            _tip(self.parent(), self.theme, "请先填写 API Host，再设为默认模型")
            return
        self.store.update_settings(self._current_id, settings)
        try:
            self.store.set_default_model(self._current_id, model_id)
        except ValueError as e:
            _tip(self.parent(), self.theme, str(e))
            return
        self.detail.models.render(self._current_id, self.store.get_settings(self._current_id).models)
        self._refresh_default_labels()

    def _on_add_provider(self):
        dlg = _AddProviderDialog(self.theme, self.store, self.parent())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if dlg.result_builtin_id:
            ok = self.store.add_builtin_provider(dlg.result_builtin_id)
            if not ok:
                _tip(self.parent(), self.theme, "该厂商已在列表中")
                return
            new_id = dlg.result_builtin_id
        elif dlg.result_custom:
            name, host, key = dlg.result_custom
            try:
                info = self.store.add_custom_provider(name, host, key)
            except ValueError as e:
                _tip(self.parent(), self.theme, str(e))
                return
            new_id = info.id
        else:
            return

        self._reload_list()
        self.sidebar.select(new_id)

    def _on_reset(self):
        if not self._current_id or self.store.is_custom(self._current_id):
            return
        self._autosave_timer.stop()
        self.store.reset_provider(self._current_id, keep_api_key=True)
        self._select_provider(self._current_id)
        _tip(self.parent(), self.theme, "已恢复默认 Host（保留 API Key；模型列表已清空，请重新拉取或添加）")

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
        settings = self.detail.collect_settings()
        n = len(settings.models)
        if n == 0:
            _tip(self.parent(), self.theme, "当前没有可清空的模型 ID")
            return
        if not _confirm(
            self.parent(),
            self.theme,
            "清空模型 ID",
            f"确定清空当前厂商的全部 {n} 个模型 ID？可稍后重新拉取或手动添加。",
        ):
            return
        self.detail.models._close_model_popup()
        self._autosave_timer.stop()
        settings.models = []
        self.store.update_settings(self._current_id, settings)
        self.detail.models.render(self._current_id, [])
        self._refresh_default_labels()
        _tip(self.parent(), self.theme, f"已清空 {n} 个模型 ID")

    def _on_add_model(self):
        if not self._current_id:
            return
        mid = _ask_text(self.parent(), self.theme, "添加模型", "模型 ID：")
        if not mid:
            return
        settings = self.detail.collect_settings()
        if any(m.model_id == mid for m in settings.models):
            _tip(self.parent(), self.theme, "模型已存在")
            return
        settings.models.append(ProviderModel(model_id=mid, enabled=False))
        self.store.update_settings(self._current_id, settings)
        self.detail.models.render(self._current_id, settings.models)
