"""AutoBee 任务详情与编辑器。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QTimer, QThread
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QLineEdit,
    QTextEdit, QComboBox, QScrollArea, QPushButton, QStackedWidget,
    QListWidget, QListWidgetItem, QDialog, QMessageBox, QCheckBox, QFrame,
)

from apscheduler.triggers.cron import CronTrigger

from tokbee.ui.styles.theme import Theme
from tokbee.ui.combo_style import apply_combo_popup_style
from tokbee.core.provider_store import ProviderStore

from wokbee.core.project_store import ProjectStore
from wokbee.gateway.manager import GatewayManager

from autobee.core.models import JobLog, ScheduledTask, TaskRunStatus, TaskType
from autobee.core.store import AutoBeeStore, MAX_LOGS_PER_TASK
from autobee.engine.nl_builder import NLBuilder
from autobee.engine.scheduler import SchedulerService, describe_cron
from autobee.ui.autobee_ui_common import _status_label
from autobee.ui.autobee_log_view import _LogDetailDialog, _LogRow


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


def _release_nl_worker(worker: _NLWorker):
    """worker 完成后再删除，避免列表保留已销毁的 Qt 对象。"""
    owner = worker.parent()
    if owner is not None:
        workers = getattr(owner, "_workers", [])
        if worker in workers:
            workers.remove(worker)
    worker.deleteLater()

class _TaskDetail(QWidget):
    """右栏：任务编辑器 + 运行历史。"""

    task_saved = Signal(str)
    task_deleted = Signal(str)

    def __init__(self, theme: Theme, store: AutoBeeStore, scheduler: SchedulerService,
                 provider_store: ProviderStore, project_store: ProjectStore,
                 gateway_manager: GatewayManager | None = None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.store = store
        self.scheduler = scheduler
        self.provider_store = provider_store
        self.project_store = project_store
        self.gateway_manager = gateway_manager
        self._edit_enabled = True
        self._task_id: str | None = None
        self._current_type = TaskType.WOKBEE
        self._log_cache: dict[str, JobLog] = {}
        self._build()
        self.scheduler.notifier.task_started.connect(self._on_task_run_started)
        self.scheduler.notifier.task_progress.connect(self._on_task_run_progress)
        self.scheduler.notifier.task_finished.connect(self._on_task_run_finished)

    # ── 构建 ───────────────────────────────────────────────
    def _build(self):
        # 空态占位页 + 编辑表单页，未选中任务时展示空态
        self._empty = self._build_empty_page()
        self._form = self._build_form()
        self._stack = QStackedWidget()
        self._stack.addWidget(self._empty)
        self._stack.addWidget(self._form)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._stack)

    def _build_empty_page(self) -> QWidget:
        c = self.theme.colors
        page = QWidget()
        page.setStyleSheet(f"background: {c['content_bg']};")
        lay = QVBoxLayout(page)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("⏰")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"font-size: 40px; color: {c['text_hint']}; background: transparent;")
        lay.addWidget(icon)
        t = QLabel("选择一个任务，或点击「新建任务」开始")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setStyleSheet(f"font-size: 14px; color: {c['text_hint']}; background: transparent; margin-top: 8px;")
        lay.addWidget(t)
        return page

    def _build_form(self) -> QWidget:
        form = QWidget()
        c = self.theme.colors
        form.setStyleSheet(f"background: {c['content_bg']};")
        form_lay = QVBoxLayout(form)
        form_lay.setContentsMargins(0, 0, 0, 0)
        form_lay.setSpacing(0)

        # 上半：可滚动表单区（避免矮窗口下推送地址被挡住）
        body = QWidget()
        body.setStyleSheet(f"background: {c['content_bg']};")
        outer = QVBoxLayout(body)
        outer.setContentsMargins(20, 16, 20, 8)
        outer.setSpacing(10)

        # 统一输入样式
        self._line_qss = f"""
            QLineEdit {{
                background: {c["input_bg"]}; color: {c["text"]};
                border: 1px solid {c["input_border"]}; border-radius: 6px;
                padding: 0 10px; font-size: 13px;
            }}
            QLineEdit:focus {{ border: 1px solid {c["input_focus_border"]}; }}
        """
        self._text_qss = f"""
            QTextEdit {{
                background: {c["input_bg"]}; color: {c["text"]};
                border: 1px solid {c["input_border"]}; border-radius: 8px;
                padding: 8px; font-size: 13px;
            }}
            QTextEdit:focus {{ border: 1px solid {c["input_focus_border"]}; }}
        """
        self._btn_qss = f"""
            QPushButton {{
                background: {c["btn_bg"]}; color: {c["text"]};
                border: none; border-radius: 6px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {c["btn_hover"]}; }}
            QPushButton:disabled {{ color: {c["text_hint"]}; }}
        """

        # ① 自然语言描述（单独一行）
        self._nl_input = QTextEdit()
        self._nl_input.setPlaceholderText(
            "用自然语言描述定时任务，如：每天上午9点给企业微信群推送问候"
        )
        self._nl_input.setFixedHeight(52)
        self._nl_input.setStyleSheet(self._text_qss)
        self._nl_input.textChanged.connect(self._update_action_btns)
        outer.addWidget(self._nl_input)

        # ② 操作栏：生成模型 + AI 生成 + 保存 + 删除 + 立即运行
        ai_row = QHBoxLayout()
        ai_row.setSpacing(8)
        self._gen_combo = QComboBox()
        self._gen_combo.setFixedHeight(34)
        self._gen_combo.setMinimumWidth(160)
        self._gen_combo.setToolTip("AI 生成所用模型")
        apply_combo_popup_style(self._gen_combo, c, rounded=True)
        ai_row.addWidget(self._gen_combo, 1)

        grey_btn = f"""
            QPushButton {{
                background: {c["btn_bg"]}; color: {c["text"]};
                border: none; border-radius: 6px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {c["btn_hover"]}; }}
            QPushButton:disabled {{ color: {c["text_hint"]}; }}
        """
        self._gen_btn = QPushButton("AI 生成")
        self._gen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._gen_btn.setFixedSize(80, 34)
        self._gen_btn.setStyleSheet(grey_btn)
        self._gen_btn.clicked.connect(self._on_nl_generate)
        ai_row.addWidget(self._gen_btn)

        self._save_btn = QPushButton("保存")
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.setFixedSize(64, 34)
        self._save_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_primary"]}; color: #ffffff;
                border: none; border-radius: 6px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {c["btn_primary_hover"]}; }}
            QPushButton:disabled {{ background: {c["btn_bg"]}; color: {c["text_hint"]}; }}
        """)
        self._save_btn.clicked.connect(self._on_save)
        ai_row.addWidget(self._save_btn)

        self._del_btn = QPushButton("删除")
        self._del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._del_btn.setFixedSize(64, 34)
        self._del_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_bg"]}; color: {c["danger"]};
                border: none; border-radius: 6px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {c["btn_hover"]}; }}
            QPushButton:disabled {{ color: {c["text_hint"]}; }}
        """)
        self._del_btn.clicked.connect(self._on_delete)
        ai_row.addWidget(self._del_btn)

        self._run_btn = QPushButton("立即运行")
        self._run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._run_btn.setFixedSize(80, 34)
        self._run_btn.setStyleSheet(grey_btn)
        self._run_btn.clicked.connect(self._on_run_now)
        ai_row.addWidget(self._run_btn)
        outer.addLayout(ai_row)
        self._update_action_btns()

        # ③ 任务名称 | 定时 cron
        row2 = QHBoxLayout()
        row2.setSpacing(12)
        self._name = QLineEdit()
        self._name.setPlaceholderText("定时任务名称")
        self._name.setFixedHeight(34)
        self._name.setStyleSheet(self._line_qss)
        row2.addWidget(self._fld("任务名称", self._name), 1)

        self._schedule = QLineEdit()
        self._schedule.setPlaceholderText("cron，如 0 9 * * *")
        self._schedule.setFixedHeight(34)
        self._schedule.setStyleSheet(self._line_qss)
        self._schedule.textChanged.connect(self._update_cron_preview)
        cron_fld, self._cron_title = self._fld_with_label("定时 (cron)", self._schedule)
        self._cron_text_value = ""
        row2.addWidget(cron_fld, 1)
        outer.addLayout(row2)

        # ④ 执行模型 + 任务类型
        row3 = QHBoxLayout()
        row3.setSpacing(12)
        self._exec_combo = QComboBox()
        self._exec_combo.setFixedHeight(34)
        apply_combo_popup_style(self._exec_combo, c, rounded=True)
        row3.addWidget(self._fld("执行模型", self._exec_combo), 1)
        self._type_combo = self._build_type_combo()
        row3.addWidget(self._fld("任务类型", self._type_combo), 1)
        outer.addLayout(row3)
        self._refill_model_combos()

        # 类型专属配置
        self._config_stack = QStackedWidget()
        self._config_stack.addWidget(self._build_text_page())
        self._config_stack.addWidget(self._build_script_page())
        self._config_stack.addWidget(self._build_wokbee_page())
        outer.addWidget(self._config_stack)

        # ⑤ 微信推送地址（有值即开启推送）
        self._webhook = QLineEdit()
        self._webhook.setPlaceholderText("企业微信群机器人 Webhook 地址（填写即开启推送）")
        self._webhook.setFixedHeight(34)
        self._webhook.setStyleSheet(self._line_qss)
        outer.addWidget(self._fld("微信推送地址", self._webhook))
        self._notify_wechat = QCheckBox("任务完成后，通过微信消息网关通知我（仅发送给绑定的微信账号）")
        self._notify_wechat.setStyleSheet(
            f"font-size: 12px; color: {c['text_secondary']}; background: transparent;"
        )
        self._update_wechat_notify_state()
        outer.addWidget(self._notify_wechat)

        # ⑥ 运行历史（接在推送地址后，随表单滚动；默认最近 10 条）
        hist_lab = QLabel(f"运行历史（最近 {MAX_LOGS_PER_TASK} 条）")
        hist_lab.setStyleSheet(
            f"font-size: 12px; color: {c['text_secondary']};"
            "background: transparent; border: none; font-weight: bold;"
        )
        outer.addWidget(hist_lab)
        self._logs = QListWidget()
        self._logs.setMinimumHeight(120)
        self._logs.setMaximumHeight(220)
        self._logs.setCursor(Qt.CursorShape.PointingHandCursor)
        self._logs.setStyleSheet(f"""
            QListWidget {{
                background: {c["input_bg"]}; border: 1px solid {c["input_border"]};
                border-radius: 8px; padding: 4px; outline: none;
            }}
            QListWidget::item {{
                border: none; border-bottom: 1px solid {c["border_light"]};
                border-radius: 4px; margin: 1px 0;
                min-height: 40px;
            }}
            QListWidget::item:selected {{
                background: {c["subnav_active"]};
            }}
            QListWidget::item:hover {{
                background: {c["subnav_hover"]};
            }}
        """)
        self._logs.itemClicked.connect(self._on_log_clicked)
        outer.addWidget(self._logs)
        outer.addStretch(1)

        # 默认任务类型：WokBee
        self._set_task_type(TaskType.WOKBEE)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.setWidget(body)
        form_lay.addWidget(scroll, stretch=1)

        return form

    def _fld(self, label: str, widget: QWidget) -> QWidget:
        w, _ = self._fld_with_label(label, widget)
        return w

    def _fld_with_label(self, label: str, widget: QWidget) -> tuple[QWidget, QLabel]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lab = QLabel(label)
        lab.setStyleSheet(
            f"font-size: 11px; color: {self.theme.colors['text_hint']};"
            "background: transparent; border: none;"
        )
        lay.addWidget(lab)
        lay.addWidget(widget)
        return w, lab

    def _build_type_combo(self) -> QComboBox:
        c = self.theme.colors
        combo = QComboBox()
        combo.setFixedHeight(34)
        apply_combo_popup_style(combo, c, rounded=True)
        combo.addItem(TaskType.TEXT.label, TaskType.TEXT.value)
        combo.addItem(TaskType.SCRIPT.label, TaskType.SCRIPT.value)
        combo.addItem(TaskType.WOKBEE.label, TaskType.WOKBEE.value)
        combo.currentIndexChanged.connect(self._on_type_changed)
        return combo

    def _build_text_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._content = QTextEdit()
        self._content.setPlaceholderText("文本正文")
        self._content.setFixedHeight(72)
        self._content.setStyleSheet(getattr(self, "_text_qss", ""))
        lay.addWidget(self._content)
        return page

    def _build_script_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._script_lang = QComboBox()
        self._script_lang.setFixedHeight(34)
        apply_combo_popup_style(self._script_lang, self.theme.colors, rounded=True)
        self._script_lang.addItem("Python", "python")
        self._script_lang.addItem("JavaScript (Node.js)", "javascript")
        self._script_lang.currentIndexChanged.connect(self._on_script_lang_changed)
        lay.addWidget(self._fld("脚本语言", self._script_lang))
        self._code = QTextEdit()
        self._code.setPlaceholderText("Python 脚本代码（用系统 Python 执行）")
        self._code.setFixedHeight(72)
        self._code.setStyleSheet(getattr(self, "_text_qss", ""))
        lay.addWidget(self._code)
        return page

    def _on_script_lang_changed(self):
        lang = self._script_lang.currentData() or "python"
        if lang == "javascript":
            self._code.setPlaceholderText("JavaScript 脚本代码（用系统 Node.js 执行）")
        else:
            self._code.setPlaceholderText("Python 脚本代码（用系统 Python 执行）")

    def _build_wokbee_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._project_id = QLineEdit()
        self._project_id.setPlaceholderText("粘贴 WokBee 项目 ID（在项目列表右键「复制项目 ID」）")
        self._project_id.setFixedHeight(34)
        self._project_id.setStyleSheet(getattr(self, "_line_qss", ""))
        lay.addWidget(self._fld("项目 ID", self._project_id))
        hint = QLabel("定时触发时将按该项目目标自动运行，无需额外填写指令。")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"font-size: 11px; color: {self.theme.colors['text_hint']};"
            "background: transparent; border: none;"
        )
        lay.addWidget(hint)
        return page

    # ── 数据刷新 ───────────────────────────────────────────
    def _refill_model_combos(self):
        models = self.provider_store.list_selectable_models()
        default = self.provider_store.resolve_default() or self.provider_store.first_resolved()

        for combo in (self._gen_combo, self._exec_combo):
            combo.blockSignals(True)
            combo.clear()
            if not models:
                combo.addItem("（暂无可用模型）", "")
            else:
                for m in models:
                    combo.addItem(
                        f"{m.provider_name} / {m.model_id}", (m.provider_id, m.model_id)
                    )
                self._pick_model(combo, default)
            combo.blockSignals(False)

    @staticmethod
    def _pick_model(combo: QComboBox, model) -> None:
        """按 ResolvedModel 或 (provider_id, model_id) 选中项；找不到则保持现状。"""
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

    # ── 详情回填 ───────────────────────────────────────────
    def set_empty(self):
        self._task_id = None
        self._stack.setCurrentWidget(self._empty)

    def prepare_new(self):
        """进入新建态：清空表单并切到编辑页。"""
        self._task_id = None
        self._clear_form()
        self._stack.setCurrentWidget(self._form)
        self._set_edit_enabled(True)
        self._name.setText("")
        self._name.setPlaceholderText("新任务名称")
        self._name.setFocus()
        self._update_action_btns()

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
        self._log_cache.clear()
        self._logs.clear()
        empty = QListWidgetItem("暂无运行历史")
        empty.setFlags(Qt.ItemFlag.NoItemFlags)
        self._logs.addItem(empty)

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
        self._task_id = task.id
        self._stack.setCurrentWidget(self._form)
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
        self._load_logs(task.id)

    def _update_action_btns(self, *, generating: bool = False):
        """按输入/是否已保存/是否运行中更新操作按钮可用状态。"""
        has_nl = bool((self._nl_input.toPlainText() or "").strip())
        saved = bool(self._task_id)
        running = bool(
            self._task_id and self.scheduler.is_task_running(self._task_id)
        )
        self._gen_btn.setEnabled(not generating and not running and has_nl)
        self._save_btn.setEnabled(not generating and not running)
        self._del_btn.setEnabled(not generating and not running and saved)
        if running:
            self._run_btn.setEnabled(False)
            self._run_btn.setText("运行中…")
        else:
            self._run_btn.setText("立即运行")
            self._run_btn.setEnabled(not generating and saved)

    def _on_task_run_started(self, task_id: str):
        if self._task_id == task_id:
            self._update_action_btns()
            self.refresh_logs()

    def _on_task_run_progress(self, task_id: str, message: str):
        if self._task_id != task_id:
            return
        self._update_action_btns()
        self.refresh_logs()

    def _on_task_run_finished(self, task_id: str, _status: str, _message: str):
        if self._task_id == task_id:
            self._update_action_btns()
            self.refresh_logs()

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
            self._update_action_btns()
        else:
            self._gen_btn.setEnabled(False)
            self._save_btn.setEnabled(False)
            self._del_btn.setEnabled(False)
            self._run_btn.setEnabled(False)

    def _load_logs(self, task_id: str):
        self._logs.clear()
        self._log_cache.clear()
        logs = self.store.list_logs(task_id, limit=MAX_LOGS_PER_TASK)
        # 新的在前
        logs = list(reversed(logs))
        if not logs:
            empty = QListWidgetItem("暂无运行历史")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self._logs.addItem(empty)
            return
        task = self.store.get(task_id)
        task_name = (task.name if task else "") or "—"
        for log in logs:
            self._log_cache[log.id] = log
            row = _LogRow(self.theme, log, task_name)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, log.id)
            item.setSizeHint(row.sizeHint())
            self._logs.addItem(item)
            self._logs.setItemWidget(item, row)

    def _on_log_clicked(self, item: QListWidgetItem):
        log_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not log_id:
            return
        log = self._log_cache.get(str(log_id))
        if not log:
            return
        task = self.store.get(self._task_id or "") if self._task_id else None
        name = (task.name if task else "") or ""
        _LogDetailDialog(self.theme, log, name, self).exec()

    def refresh_logs(self):
        if self._task_id:
            self._load_logs(self._task_id)

    # ── 信号处理 ───────────────────────────────────────────
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

    def _on_run_now(self):
        if not self._task_id:
            return
        if self.scheduler.is_task_running(self._task_id):
            return
        self._run_btn.setEnabled(False)
        self._run_btn.setText("运行中…")
        try:
            started = self.scheduler.run_now(self._task_id)
            if not started:
                self._update_action_btns()
        except Exception:
            self._update_action_btns()

    def _on_nl_generate(self):
        text = (self._nl_input.toPlainText() or "").strip()
        if not text:
            return
        model = None
        gd = self._gen_combo.currentData()
        if isinstance(gd, tuple):
            model = self.provider_store.resolve(gd[0], gd[1])
        if not model:
            model = self.provider_store.resolve_default() or self.provider_store.first_resolved()
        if not model:
            return
        self._update_action_btns(generating=True)
        self._workers = list(getattr(self, "_workers", []))
        w = _NLWorker(NLBuilder(self.provider_store), text, model, self)
        w.done.connect(self._on_nl_done)
        w.finished.connect(lambda worker=w: _release_nl_worker(worker))
        self._workers.append(w)
        w.start()

    def _on_nl_done(self, result):
        self._update_action_btns()
        if isinstance(result, Exception):
            return
        data = result or {}
        config = data.get("config") or {}
        self._name.setText(data.get("name") or "")
        self._schedule.setText(data.get("schedule") or "")
        self._cron_text_value = data.get("cron_text") or ""
        self._update_cron_preview()
        ttype = (data.get("type") or "text").lower()
        if ttype not in (TaskType.TEXT.value, TaskType.SCRIPT.value, TaskType.WOKBEE.value):
            ttype = TaskType.TEXT.value
        idx = [TaskType.TEXT.value, TaskType.SCRIPT.value, TaskType.WOKBEE.value].index(ttype)
        self._type_combo.setCurrentIndex(idx)
        self._config_stack.setCurrentIndex(idx)
        self._content.setPlainText(str(config.get("content") or ""))
        self._code.setPlainText(str(config.get("code") or ""))
        lang = str(config.get("script_lang") or "python").lower()
        self._script_lang.setCurrentIndex(1 if lang in ("js", "javascript", "node") else 0)
        self._on_script_lang_changed()
        if config.get("project_id"):
            self._project_id.setText(str(config["project_id"]))
        # 推送：有 webhook 即填入
        webhook = str(config.get("webhook_url") or "")
        if webhook:
            self._webhook.setText(webhook)
        self._set_edit_enabled(True)

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
        gd = self._gen_combo.currentData()
        if isinstance(gd, tuple):
            data["gen_provider"], data["gen_model_id"] = gd
        ed = self._exec_combo.currentData()
        if isinstance(ed, tuple):
            data["exec_provider"], data["exec_model_id"] = ed
        return data

    @staticmethod
    def _to_int(text: str, default: int) -> int:
        try:
            return max(1, int(str(text).strip() or default))
        except ValueError:
            return default

    def _on_save(self):
        task_type = TaskType(self._type_combo.currentData() or TaskType.TEXT.value)
        data = self._collect(task_type)
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
                QMessageBox.warning(
                    self, "缺少关联项目",
                    "WokBee 任务需要关联一个项目，请先在下方填写项目 ID。",
                )
                self._project_id.setFocus()
                return
            if self.project_store.get(pid) is None:
                QMessageBox.warning(
                    self, "项目不存在",
                    f"项目 {pid} 不存在，请重新选择。",
                )
                self._project_id.setFocus()
                return

        if self._task_id and self.store.get(self._task_id):
            task = self.store.get(self._task_id)
            for k, v in data.items():
                setattr(task, k, v)
            task.touch()
            self.store.save_task(task)
            task_id = task.id
        else:
            task = self.store.create(**data)
            task_id = task.id
        self._task_id = task_id
        self.scheduler.add_or_update(task)
        self.task_saved.emit(task_id)
        self._update_action_btns()

    def _on_delete(self):
        if not self._task_id:
            return
        if not self.store.get(self._task_id):
            return
        c = self.theme.colors
        dlg = QDialog(self)
        dlg.setWindowTitle("删除任务")
        dlg.setFixedSize(360, 140)
        dlg.setStyleSheet(f"background: {c['content_bg']};")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(12)
        msg = QLabel("确定删除该定时任务？此操作不可撤销。")
        msg.setWordWrap(True)
        msg.setStyleSheet(f"font-size: 14px; color: {c['text']}; background: transparent; border: none;")
        lay.addWidget(msg)
        lay.addStretch()
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch()
        cancel = QPushButton("取消")
        cancel.setFixedSize(72, 34)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(secondary_btn_qss(c))
        cancel.clicked.connect(dlg.reject)
        ok = QPushButton("删除")
        ok.setFixedSize(72, 34)
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_bg"]}; color: {c["danger"]};
                border: none; border-radius: 6px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {c["btn_hover"]}; }}
        """)
        ok.clicked.connect(dlg.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        tid = self._task_id
        self.store.delete_task(tid)
        self.scheduler.remove(tid)
        self.set_empty()
        self.task_deleted.emit(tid)


__all__ = ["_TaskDetail"]
