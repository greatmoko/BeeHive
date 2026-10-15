"""自然语言生成控制器：持有线程直到 finished 后释放。"""
from __future__ import annotations
from PySide6.QtCore import Signal, QThread
from beehive.features.autobee.core.models import TaskType
from beehive.features.autobee.engine.nl_builder import NLBuilder

from PySide6.QtCore import QObject

class _NLWorker(QThread):
    """自然语言生成定时配置：后台跑 AI，避免冻结 UI。"""

    done = Signal(object)  # dict | Exception

    def __init__(self, builder: NLBuilder, text: str, model, parent=None):
        super().__init__(parent)
        self._builder = builder
        self._text = text
        self._model = model

    def run(self):
        try:
            data = self._builder.generate(self._text, self._model)
            self.done.emit(data)
        except Exception as e:
            self.done.emit(e)


class TaskGenerationController(QObject):
    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self._workers = []

    def _on_nl_generate(self):
        if self.is_running():
            return
        text = (self.view.form._nl_input.toPlainText() or "").strip()
        if not text:
            return
        model = None
        gd = self.view.form._gen_combo.current_model()
        if gd[1]:
            model = self.view.provider_store.resolve(gd[0], gd[1])
        if not model:
            model = self.view.provider_store.resolve_default() or self.view.provider_store.first_resolved()
        if not model:
            return
        self.view.actions._update_action_btns(generating=True)
        w = _NLWorker(NLBuilder(self.view.provider_store), text, model, self)
        w.done.connect(self._on_nl_done)
        w.finished.connect(self._on_worker_stopped)
        w.finished.connect(w.deleteLater)
        self._workers.append(w)
        w.start()

    def _on_nl_done(self, result):
        self.view.actions._update_action_btns()
        if isinstance(result, Exception):
            return
        data = result or {}
        config = data.get("config") or {}
        self.view.form._name.setText(data.get("name") or "")
        self.view.form._schedule.setText(data.get("schedule") or "")
        self.view.form._cron_text_value = data.get("cron_text") or ""
        self.view.form._update_cron_preview()
        ttype = (data.get("type") or "text").lower()
        if ttype not in (TaskType.TEXT.value, TaskType.SCRIPT.value, TaskType.WOKBEE.value):
            ttype = TaskType.TEXT.value
        idx = [TaskType.TEXT.value, TaskType.SCRIPT.value, TaskType.WOKBEE.value].index(ttype)
        self.view.form._type_combo.setCurrentIndex(idx)
        self.view.form._config_stack.setCurrentIndex(idx)
        self.view.form._content.setPlainText(str(config.get("content") or ""))
        self.view.form._code.setPlainText(str(config.get("code") or ""))
        lang = str(config.get("script_lang") or "python").lower()
        self.view.form._script_lang.setCurrentIndex(1 if lang in ("js", "javascript", "node") else 0)
        self.view.form._on_script_lang_changed()
        if config.get("project_id"):
            self.view.form._project_id.setText(str(config["project_id"]))
        # 推送：有 webhook 即填入
        webhook = str(config.get("webhook_url") or "")
        if webhook:
            self.view.form._webhook.setText(webhook)
        self.view.form._set_edit_enabled(True)

    def _on_worker_stopped(self):
        worker = self.sender()
        if worker in self._workers:
            self._workers.remove(worker)
        self.view.actions._update_action_btns()

    def is_running(self):
        return any(worker.isRunning() for worker in self._workers)

    def shutdown(self):
        return not self.is_running()
