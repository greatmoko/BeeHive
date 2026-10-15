"""任务校验、保存、删除和立即运行。"""
from __future__ import annotations
from apscheduler.triggers.cron import CronTrigger
from beehive.features.autobee.core.models import TaskType

from PySide6.QtCore import QObject
from beehive.common.ui.dialogs.message import _confirm, show_tip

class TaskActions(QObject):
    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def _update_action_btns(self, *, generating: bool = False):
        """按输入/是否已保存/是否运行中更新操作按钮可用状态。"""
        generating = generating or self.view.generation.is_running()
        has_nl = bool((self.view.form._nl_input.toPlainText() or "").strip())
        saved = bool(self.view._task_id)
        running = bool(
            self.view._task_id and self.view.scheduler.is_task_running(self.view._task_id)
        )
        self.view.form._gen_btn.setEnabled(not generating and not running and has_nl)
        self.view.form._save_btn.setEnabled(not generating and not running)
        self.view.form._del_btn.setEnabled(not generating and not running and saved)
        if running:
            self.view.form._run_btn.setEnabled(False)
            self.view.form._run_btn.setText("运行中…")
        else:
            self.view.form._run_btn.setText("立即运行")
            self.view.form._run_btn.setEnabled(not generating and saved)

    def _on_run_now(self):
        if not self.view._task_id:
            return
        if self.view.scheduler.is_task_running(self.view._task_id):
            return
        self.view.form._run_btn.setEnabled(False)
        self.view.form._run_btn.setText("运行中…")
        try:
            started = self.view.scheduler.run_now(self.view._task_id)
            if not started:
                self._update_action_btns()
        except Exception:
            self._update_action_btns()

    def _on_save(self):
        task_type = TaskType(self.view.form._type_combo.currentData() or TaskType.TEXT.value)
        data = self.view.form._collect(task_type)
        name = data["name"]
        schedule = data["schedule"]
        if not name:
            return
        # 校验 cron
        try:
            CronTrigger.from_crontab(schedule)
        except (ValueError, TypeError):
            return
        # 校验类型必填
        if task_type == TaskType.SCRIPT and not data["code"]:
            return
        if task_type == TaskType.WOKBEE:
            pid = data["project_id"]
            if not pid:
                # 不再静默放弃：提示用户补齐项目（NL 生成也可能缺 project_id）
                from beehive.common.ui.dialogs.message import show_tip as tip
                tip(
                    self.view, self.view.theme,
                    "WokBee 任务需要关联一个项目，请先在下方填写项目 ID。",
                    title="缺少关联项目",
                )
                self.view.form._project_id.setFocus()
                return
            if self.view.project_store.get(pid) is None:
                from beehive.common.ui.dialogs.message import show_tip as tip
                tip(
                    self.view, self.view.theme,
                    f"项目 {pid} 不存在，请重新选择。",
                    title="项目不存在",
                )
                self.view.form._project_id.setFocus()
                return

        if self.view._task_id and self.view.store.get(self.view._task_id):
            task = self.view.store.get(self.view._task_id)
            for k, v in data.items():
                setattr(task, k, v)
            task.touch()
            self.view.store.save_task(task)
            task_id = task.id
        else:
            task = self.view.store.create(**data)
            task_id = task.id
        self.view._task_id = task_id
        self.view.scheduler.add_or_update(task)
        self.view.task_saved.emit(task_id)
        self._update_action_btns()

    def _on_delete(self):
        if not self.view._task_id:
            return
        if not self.view.store.get(self.view._task_id):
            return
        if not _confirm(
            self.view, self.view.theme, "删除任务",
            "确定删除该定时任务？此操作不可撤销。",
        ):
            return
        tid = self.view._task_id
        self.view.store.delete_task(tid)
        self.view.scheduler.remove(tid)
        self.view.set_empty()
        self.view.task_deleted.emit(tid)
