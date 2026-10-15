"""终端、模型/工具超时与节流等系统运行参数表单。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QComboBox, QCheckBox, QSpinBox
from beehive.common.ui.styles.selectors import apply_combo_popup_style
from beehive.common.ui.styles.metrics import DEFAULT_COMBO_HEIGHT
from beehive.common.ui.styles.inputs import rounded_spin_qss
from beehive.common.ui.styles.checks import checkbox_qss
from beehive.common.ui.styles.labels import hint_label_qss
from beehive.system.settings.ui.form_helpers import section_label
from beehive.system.settings.terminal_apps import detect_terminal_apps


class RuntimeSettingsForm(QWidget):
    def __init__(self, theme, settings, parent=None):
        super().__init__(parent)
        self.theme, self.settings = theme, settings
        c = theme.colors
        bl = QVBoxLayout(self)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(18)
        spin_qss = rounded_spin_qss(c)
        lbl_qss = f"font-size: 13px; color: {c['text']};"
        bl.addWidget(section_label("终端运行软件", self.theme.colors))
        self._terminal_combo = QComboBox()
        apply_combo_popup_style(
            self._terminal_combo, c, rounded=True,
            fixed_width=300, fixed_height=DEFAULT_COMBO_HEIGHT,
        )
        bl.addWidget(self._terminal_combo, alignment=Qt.AlignmentFlag.AlignLeft)
        terminal_hint = QLabel(
            "Agent 运行命令行时使用的终端；未选择时默认使用 cmd。"
        )
        terminal_hint.setWordWrap(True)
        terminal_hint.setStyleSheet(hint_label_qss(c))
        bl.addWidget(terminal_hint)

        bl.addWidget(section_label("AI 调用节流", self.theme.colors))
        ai_int_row = QHBoxLayout()
        ai_int_row.setSpacing(10)
        ai_int_lbl = QLabel("调用最小间隔（毫秒）")
        ai_int_lbl.setStyleSheet(lbl_qss)
        ai_int_row.addWidget(ai_int_lbl)
        self._ai_interval = QSpinBox()
        self._ai_interval.setRange(0, 60000)
        self._ai_interval.setSingleStep(500)
        self._ai_interval.setFixedWidth(120)
        self._ai_interval.setSuffix(" ms")
        self._ai_interval.setStyleSheet(spin_qss)
        self._ai_interval.setToolTip("每次调用模型或工具前等待")
        ai_int_row.addWidget(self._ai_interval)
        ai_int_row.addStretch()
        bl.addLayout(ai_int_row)
        ai_int_hint = QLabel(
            "每次调用模型、每次调用工具（execute / 读写 / 搜索等）开始前都会等待该毫秒数。"
            "设 0 表示关掉；设 2000 则每一步先等 2 秒再执行。并行工具会各自先等再一起跑。"
        )
        ai_int_hint.setWordWrap(True)
        ai_int_hint.setStyleSheet(hint_label_qss(c))
        bl.addWidget(ai_int_hint)

        bl.addWidget(section_label("工具执行超时", self.theme.colors))
        timeout_row = QHBoxLayout()
        timeout_row.setSpacing(10)
        timeout_lbl = QLabel("单工具超时（秒）")
        timeout_lbl.setStyleSheet(lbl_qss)
        timeout_row.addWidget(timeout_lbl)
        self._tool_timeout = QSpinBox()
        self._tool_timeout.setRange(5, 3600)
        self._tool_timeout.setSingleStep(10)
        self._tool_timeout.setFixedWidth(120)
        self._tool_timeout.setSuffix(" s")
        self._tool_timeout.setStyleSheet(spin_qss)
        self._tool_timeout.setToolTip(
            "单个工具执行的【默认】超时；超过该时长即终止并返回失败，交由 AI 接管。"
            "AI 调用工具时可传 timeout_seconds 覆盖单次等待时长（默认值仅在 AI 未指定时生效）"
        )
        timeout_row.addWidget(self._tool_timeout)
        timeout_row.addStretch()
        bl.addLayout(timeout_row)
        timeout_hint = QLabel(
            "此值为【默认】超时：联网工具命中即被真正终止，纯 Python 工具超时则返回失败、退出后台线程。"
            "AI 调用工具时可传 timeout_seconds 覆盖单次等待（如下载大文件传更大值）；"
            "ask_user/task（子代理）/request_access 不套此超时。"
        )
        timeout_hint.setWordWrap(True)
        timeout_hint.setStyleSheet(hint_label_qss(c))
        bl.addWidget(timeout_hint)

        bl.addWidget(section_label("模型调用超时", self.theme.colors))
        mt_row = QHBoxLayout()
        mt_row.setSpacing(10)
        mt_lbl = QLabel("单次模型请求（秒）")
        mt_lbl.setStyleSheet(lbl_qss)
        mt_row.addWidget(mt_lbl)
        self._model_timeout = QSpinBox()
        self._model_timeout.setRange(10, 3600)
        self._model_timeout.setSingleStep(30)
        self._model_timeout.setFixedWidth(120)
        self._model_timeout.setSuffix(" s")
        self._model_timeout.setStyleSheet(spin_qss)
        self._model_timeout.setToolTip("模型单次请求超过该时长即中止本轮，避免运行永久卡在「运行中」")
        mt_row.addWidget(self._model_timeout)
        mt_row.addStretch()
        bl.addLayout(mt_row)
        mt_hint = QLabel(
            "流式响应按「相邻字节间隔」计数，持续出 token 的正常长回复不受限；"
            "仅真正无响应的挂死连接会在这之后结束本轮。可配合「终止」让任务尽快收尾。"
        )
        mt_hint.setWordWrap(True)
        mt_hint.setStyleSheet(hint_label_qss(c))
        bl.addWidget(mt_hint)

        ds_box = QVBoxLayout()
        ds_box.setSpacing(4)
        self._enable_search = QCheckBox("启用 DeepSeek 服务端搜索工具")
        self._enable_search.setStyleSheet(checkbox_qss(c))
        self._enable_search.setToolTip("需 DeepSeek 官方 API")
        ds_box.addWidget(self._enable_search)
        ds_hint = QLabel(
            "把 DeepSeek 官方联网搜索包成工具给 Agent 用（多轮检索+引用）。"
            "需在「厂商设置」添加 DeepSeek 官方 API 才能使用；使用其他模型时也可以调用该工具。"
        )
        ds_hint.setWordWrap(True)
        ds_hint.setFrameShape(QFrame.Shape.NoFrame)
        ds_hint.setStyleSheet(hint_label_qss(c))
        ds_box.addWidget(ds_hint)
        bl.addLayout(ds_box)

    def load(self):
        self._ai_interval.setValue(self.settings.ai_interval_ms)
        self._tool_timeout.setValue(self.settings.tool_timeout_seconds)
        self._model_timeout.setValue(self.settings.model_timeout_seconds)
        self._enable_search.setChecked(self.settings.enable_deepseek_search)
        self._reload_terminals()

    def apply(self):
        self.settings.ai_interval_ms = self._ai_interval.value()
        self.settings.tool_timeout_seconds = self._tool_timeout.value()
        self.settings.model_timeout_seconds = self._model_timeout.value()
        self.settings.enable_deepseek_search = self._enable_search.isChecked()
        self.settings.terminal_app = str(self._terminal_combo.currentData() or "cmd")

    def _reload_terminals(self):
        """填充终端下拉框：系统内可用命令行工具 + 未选中时默认 cmd。"""
        self._terminal_combo.blockSignals(True)
        self._terminal_combo.clear()
        terminals = detect_terminal_apps()
        for key, exe in terminals:
            self._terminal_combo.addItem(exe, key)
        current = self.settings.terminal_app
        idx = 0
        for i, (key, _exe) in enumerate(terminals):
            if key == current:
                idx = i
                break
        self._terminal_combo.setCurrentIndex(idx)
        self._terminal_combo.blockSignals(False)
