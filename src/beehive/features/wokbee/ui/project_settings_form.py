"""项目根目录、审批、模型回退与执行预算的业务表单。"""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QSpinBox, QFileDialog
from beehive.common.ui.styles.inputs import rounded_lineedit_qss, rounded_spin_qss
from beehive.common.ui.styles.buttons import secondary_btn_qss
from beehive.common.ui.styles.labels import hint_label_qss
from beehive.common.ui.widgets.model_selector import ModelSelectorButton
from beehive.system.settings.ui.form_helpers import section_label
from beehive.system.providers.provider_store import ProviderStore
from beehive.features.wokbee.ui.dialogs import build_approval_checkboxes, apply_flags_to_checks, flags_from_checks


class ProjectSettingsForm(QWidget):
    def __init__(self, theme, settings, parent=None):
        super().__init__(parent)
        self.theme, self.settings = theme, settings
        self._provider_store = ProviderStore()
        c = theme.colors
        bl = QVBoxLayout(self)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(18)
        bl.addWidget(section_label("工作区根目录", self.theme.colors))
        ws_row = QHBoxLayout()
        self._ws_edit = QLineEdit()
        self._ws_edit.setFixedHeight(34)
        self._ws_edit.setPlaceholderText("选择 WokBee 工作区根目录…")
        self._ws_edit.setStyleSheet(rounded_lineedit_qss(c))
        ws_row.addWidget(self._ws_edit, stretch=1)
        browse = QPushButton("浏览…")
        browse.setFixedSize(72, 34)
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        browse.setStyleSheet(secondary_btn_qss(self.theme.colors))
        browse.clicked.connect(self._browse_workspace)
        ws_row.addWidget(browse)
        bl.addLayout(ws_row)
        hint = QLabel(
            "新建项目将创建：archives|uploads|output|memory|.project…；"
            "交付物→output/；上传→uploads/（归档时保留）；"
            "经验→memory/exp_时间戳.md（只加载最新）；事件日志→memory/events.jsonl；"
            "Agent 禁止访问 archives/；Skills 全局挂载不复制"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(hint_label_qss(c))
        bl.addWidget(hint)
        bl.addWidget(section_label("默认审核策略（勾选 = 免审）", self.theme.colors))
        approval_box, self._approval_checks = build_approval_checkboxes(self.theme)
        bl.addWidget(approval_box)
        ap_hint = QLabel(
            "未勾选的级别在执行时需要人工审批。"
            "新项目会继承这些勾选，互不影响；改全局不会自动改已有项目。"
        )
        ap_hint.setWordWrap(True)
        ap_hint.setStyleSheet(hint_label_qss(c))
        bl.addWidget(ap_hint)

        bl.addWidget(section_label("默认模型（OpenAI 风格 API）", self.theme.colors))
        self._model_combo = ModelSelectorButton(
            self.theme, width=300,
            placeholder="（未指定，使用厂商默认）", hint_tooltip="请选择模型",
        )
        bl.addWidget(self._model_combo, alignment=Qt.AlignmentFlag.AlignLeft)
        model_hint = QLabel(
            "新建项目优先使用「厂商设置」里带「默认」徽章的模型；"
            "此处仅在未设置厂商默认时作为回退。Key / Host 仍在厂商设置中维护。"
        )
        model_hint.setWordWrap(True)
        model_hint.setStyleSheet(hint_label_qss(c))
        bl.addWidget(model_hint)

        bl.addWidget(section_label("执行上限", self.theme.colors))
        spin_qss = rounded_spin_qss(c)
        lbl_qss = f"font-size: 13px; color: {c['text']};"
        limits = QHBoxLayout()
        limits.setSpacing(16)
        max_steps_lbl = QLabel("运行最大步数")
        max_steps_lbl.setStyleSheet(lbl_qss)
        limits.addWidget(max_steps_lbl)
        self._run_max_steps = QSpinBox()
        self._run_max_steps.setRange(1, 500)
        self._run_max_steps.setFixedWidth(100)
        self._run_max_steps.setStyleSheet(spin_qss)
        self._run_max_steps.setToolTip("运行总步数上限，管线脚本与 Agent 共用（默认 128）")
        limits.addWidget(self._run_max_steps)
        max_par_lbl = QLabel("最大并行工具")
        max_par_lbl.setStyleSheet(lbl_qss)
        limits.addWidget(max_par_lbl)
        self._max_parallel = QSpinBox()
        self._max_parallel.setRange(1, 16)
        self._max_parallel.setFixedWidth(100)
        self._max_parallel.setStyleSheet(spin_qss)
        limits.addWidget(self._max_parallel)
        max_ph_lbl = QLabel("聊天最大步数")
        max_ph_lbl.setStyleSheet(lbl_qss)
        limits.addWidget(max_ph_lbl)
        self._chat_max_steps = QSpinBox()
        self._chat_max_steps.setRange(1, 500)
        self._chat_max_steps.setFixedWidth(100)
        self._chat_max_steps.setStyleSheet(spin_qss)
        self._chat_max_steps.setToolTip("单轮聊天步数上限（默认 64）")
        limits.addWidget(self._chat_max_steps)
        limits.addStretch()
        bl.addLayout(limits)
        phase_hint = QLabel(
            "运行默认 128 步，聊天默认 64 步。"
            "运行时由 AI 主导：启动后先查看 memory/ 是否有最新经验文件，"
            "非首次运行按经验记载的成功路径调取并运行 memory/ 下的可复用脚本；"
            "运行结束后按需更新经验文档。"
        )
        phase_hint.setWordWrap(True)
        phase_hint.setStyleSheet(hint_label_qss(c))
        bl.addWidget(phase_hint)

    def load(self):
        self._ws_edit.setText(str(self.settings.workspace_root))
        apply_flags_to_checks(self._approval_checks, self.settings.approval)
        self._run_max_steps.setValue(self.settings.run_max_steps)
        self._max_parallel.setValue(self.settings.max_parallel_tools)
        self._chat_max_steps.setValue(self.settings.chat_max_steps)
        self._reload_models()

    def apply(self):
        self.settings.workspace_root = self._ws_edit.text().strip()
        self.settings.approval = flags_from_checks(self._approval_checks)
        self.settings.run_max_steps = self._run_max_steps.value()
        self.settings.max_parallel_tools = self._max_parallel.value()
        self.settings.chat_max_steps = self._chat_max_steps.value()
        pair = self._model_combo.current_model()
        self.settings.default_provider, self.settings.default_model_id = pair

    def _browse_workspace(self):
        start = self._ws_edit.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "选择 WokBee 工作区根目录", start)
        if path:
            self._ws_edit.setText(path)

    def _reload_models(self):
        options = [("（未指定，使用厂商默认）", ("", ""))]
        try:
            models = self._provider_store.list_selectable_models()
        except Exception:
            models = []
        options += [
            (f"{m.provider_name} / {m.model_id}", (m.provider_id, m.model_id))
            for m in models
        ]
        current = (self.settings.default_provider, self.settings.default_model_id)
        if not current[1] or not any(pair == current for _, pair in options):
            current = ("", "")  # 未指定或已失效：回退「未指定」占位
        self._model_combo.set_options(options, current)
