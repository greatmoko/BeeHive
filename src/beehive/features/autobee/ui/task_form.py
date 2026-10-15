"""任务表单回填、取值与编辑状态。"""
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout
from beehive.features.autobee.core.models import ScheduledTask, TaskType
from beehive.features.autobee.engine.cron_description import describe_cron
from beehive.common.ui.widgets.model_selector import ModelSelectorButton
from beehive.features.autobee.ui.task_form_layout import build_task_form

class TaskForm(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.theme = owner.theme
        self.provider_store = owner.provider_store
        self.gateway_manager = owner.gateway_manager
        self._edit_enabled = True
        self._current_type = TaskType.WOKBEE
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(build_task_form(self))

    def _refill_model_combos(self):
        try:
            models = self.provider_store.list_selectable_models()
        except Exception:
            models = []
        default = self.provider_store.resolve_default() or self.provider_store.first_resolved()
        options = [
            (f"{m.provider_name} / {m.model_id}", (m.provider_id, m.model_id))
            for m in models
        ]
        for combo in (self._gen_combo, self._exec_combo):
            current = ("", "")
            if models and default:
                current = (default.provider_id, default.model_id)
            combo.set_options(options, current)

    def _pick_model(self, combo: ModelSelectorButton, model) -> None:
        """按 ResolvedModel 或 (provider_id, model_id) 选中项。"""
        if model is None:
            return
        if hasattr(model, "provider_id"):
            model = (model.provider_id, model.model_id)
        combo.set_current((str(model[0] or ""), str(model[1] or "")))
        if model is None:
            return
        if hasattr(model, "provider_id"):
            pid, mid = model.provider_id, model.model_id
        elif isinstance(model, tuple) and len(model) == 2:
            pid, mid = model
        else:
            return
        for i in range(combo.count()):
            data = combo.itemData(i)
            if isinstance(data, tuple) and data[0] == pid and data[1] == mid:
                combo.setCurrentIndex(i)
                return

    def _clear_form(self):
        self._name.setText("")
        self._nl_input.setPlainText("")
        self._cron_text_value = ""
        self._content.setPlainText("")
        self._code.clear()
        self._script_lang.setCurrentIndex(0)
        self._on_script_lang_changed()
        self._schedule.setText("*/30 * * * *")
        self._project_id.clear()
        self._set_task_type(TaskType.WOKBEE)
        self._refill_model_combos()
        self._update_cron_preview()
        self._webhook.clear()
        self._notify_wechat.setChecked(False)

    def _update_wechat_notify_state(self):
        connected = bool(
            self.gateway_manager
            and self.gateway_manager.is_channel_connected("wechat")
        )
        self._notify_wechat.setEnabled(connected and self._edit_enabled)
        self._notify_wechat.setToolTip(
            "微信消息网关已连接"
            if connected
            else "请先到 AIConfig → 消息网关连接微信"
        )

    def load(self, task: ScheduledTask):
        self._name.setText(task.name)
        self._nl_input.setPlainText(task.description)
        self._schedule.setText(task.schedule)
        self._cron_text_value = task.cron_text or ""
        self._update_cron_preview()

        idx = [TaskType.TEXT, TaskType.SCRIPT, TaskType.WOKBEE].index(task.task_type)
        self._type_combo.setCurrentIndex(idx)
        self._config_stack.setCurrentIndex(idx)

        self._content.setPlainText(task.content)
        self._code.setPlainText(task.code)
        lang = getattr(task, "script_lang", "python") or "python"
        lang_idx = 1 if lang == "javascript" else 0
        self._script_lang.setCurrentIndex(lang_idx)
        self._on_script_lang_changed()
        self._project_id.setText(task.project_id or "")
        self._webhook.setText(task.webhook_url)
        self._notify_wechat.setChecked(bool(getattr(task, "notify_wechat", False)))

        self._refill_model_combos()
        if task.gen_provider and task.gen_model_id:
            self._pick_model(self._gen_combo, (task.gen_provider, task.gen_model_id))
        if task.exec_provider and task.exec_model_id:
            self._pick_model(self._exec_combo, (task.exec_provider, task.exec_model_id))

        self._set_edit_enabled(True)

    def _set_edit_enabled(self, enabled: bool):
        self._edit_enabled = enabled
        for w in [self._name, self._nl_input, self._content,
                  self._code, self._script_lang,
                  self._schedule, self._gen_combo, self._exec_combo,
                  self._type_combo, self._project_id, self._webhook,
                  self._notify_wechat]:
            w.setEnabled(enabled)
        self._update_wechat_notify_state()
        if enabled:
            self.owner.actions._update_action_btns()
        else:
            self._gen_btn.setEnabled(False)
            self._save_btn.setEnabled(False)
            self._del_btn.setEnabled(False)
            self._run_btn.setEnabled(False)

    def _set_task_type(self, ttype: TaskType):
        """切换任务类型下拉与配置页（含默认 WokBee）。"""
        order = [TaskType.TEXT, TaskType.SCRIPT, TaskType.WOKBEE]
        idx = order.index(ttype) if ttype in order else 0
        self._type_combo.blockSignals(True)
        self._type_combo.setCurrentIndex(idx)
        self._type_combo.blockSignals(False)
        self._config_stack.setCurrentIndex(idx)
        self._current_type = ttype

    def _on_type_changed(self):
        idx = self._type_combo.currentIndex()
        self._config_stack.setCurrentIndex(idx)
        data = self._type_combo.currentData()
        try:
            self._current_type = TaskType(data) if data else TaskType.WOKBEE
        except ValueError:
            self._current_type = TaskType.WOKBEE

    def _update_cron_preview(self):
        expr = (self._schedule.text() or "").strip()
        hint = describe_cron(expr)
        if expr and hint:
            self._cron_title.setText(f"定时 (cron) · {hint}")
        else:
            self._cron_title.setText("定时 (cron)")

    def _collect(self, task_type: TaskType) -> dict:
        """从控件收集当前表单值。"""
        webhook = (self._webhook.text() or "").strip()
        data = {
            "name": (self._name.text() or "").strip(),
            "description": (self._nl_input.toPlainText() or "").strip(),
            "task_type": task_type,
            "schedule": (self._schedule.text() or "").strip(),
            "cron_text": (self._cron_text_value or "").strip(),
            "gen_provider": "", "gen_model_id": "",
            "exec_provider": "", "exec_model_id": "",
            "content": (self._content.toPlainText() or "").strip(),
            "use_ai": False,
            "code": (self._code.toPlainText() or "").strip(),
            "script_lang": self._script_lang.currentData() or "python",
            "timeout_s": 120,
            "project_id": (self._project_id.text() or "").strip(),
            "user_message": "",
            "max_steps": 40,
            "push_wecom": bool(webhook),
            "webhook_url": webhook,
            "msgtype": "text",
            "mention": "",
            "notify_wechat": self._notify_wechat.isChecked(),
        }
        gd = self._gen_combo.current_model()
        if gd[1]:
            data["gen_provider"], data["gen_model_id"] = gd
        ed = self._exec_combo.current_model()
        if ed[1]:
            data["exec_provider"], data["exec_model_id"] = ed
        return data

    def _on_script_lang_changed(self):
        lang = self._script_lang.currentData() or "python"
        if lang == "javascript":
            self._code.setPlaceholderText("JavaScript 脚本代码（用系统 Node.js 执行）")
        else:
            self._code.setPlaceholderText("Python 脚本代码（用系统 Python 执行）")
