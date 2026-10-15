"""远程模型拉取的线程生命周期及结果合并。"""
from PySide6.QtCore import QObject
from beehive.system.providers.provider_models import ProviderModel
from beehive.system.providers.ui.provider_dialogs import _tip, _FetchModelsWorker

class ProviderFetchController(QObject):


    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self._fetch_worker = None

    def start(self):
        if not self.editor._current_id:
            return
        host = self.editor.detail._host_edit.text().strip()
        if not host:
            _tip(self.editor.parent(), self.editor.theme, "请先填写 API Host")
            return
        self.editor._autosave_now()
        self.editor.detail._query_btn.setEnabled(False)
        self.editor.detail._query_btn.setText("拉取中…")
        self._fetch_worker = _FetchModelsWorker(host, self.editor.detail._key_edit.text().strip(), self)
        self._fetch_worker.finished_ok.connect(self._on_fetch_ok)
        self._fetch_worker.finished_err.connect(self._on_fetch_err)
        self._fetch_worker.start()

    def _on_fetch_ok(self, model_ids: list):
        self.editor.detail._query_btn.setEnabled(True)
        self.editor.detail._query_btn.setText("拉取远程模型")
        settings = self.editor.detail.collect_settings()
        existing = {m.model_id for m in settings.models}
        for mid in model_ids:
            if mid not in existing:
                settings.models.append(ProviderModel(model_id=mid, enabled=False))
        self.editor.store.update_settings(self.editor._current_id, settings)
        self.editor.detail.models.render(self.editor._current_id, settings.models)
        _tip(self.editor.parent(), self.editor.theme, f"已获取 {len(model_ids)} 个模型，请勾选需要启用的项")

    def _on_fetch_err(self, err: str):
        self.editor.detail._query_btn.setEnabled(True)
        self.editor.detail._query_btn.setText("拉取远程模型")
        _tip(self.editor.parent(), self.editor.theme, f"拉取失败：{err}")
