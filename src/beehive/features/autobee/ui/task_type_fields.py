"""文本、脚本和项目类型字段的布局。"""
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QLineEdit, QTextEdit, QComboBox
from beehive.common.ui.styles.selectors import apply_combo_popup_style
from beehive.common.ui.styles.labels import hint_label_qss
from beehive.features.autobee.core.models import TaskType

def fld(form, label: str, widget: QWidget) -> QWidget:
    w, _ = fld_with_label(form, label, widget)
    return w

def fld_with_label(form, label: str, widget: QWidget) -> tuple[QWidget, QLabel]:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lab = QLabel(label)
    lab.setStyleSheet(hint_label_qss(form.theme.colors))
    lay.addWidget(lab)
    lay.addWidget(widget)
    return w, lab

def build_type_combo(form) -> QComboBox:
    c = form.theme.colors
    combo = QComboBox()
    combo.setFixedHeight(34)
    apply_combo_popup_style(combo, c, rounded=True)
    combo.addItem(TaskType.TEXT.label, TaskType.TEXT.value)
    combo.addItem(TaskType.SCRIPT.label, TaskType.SCRIPT.value)
    combo.addItem(TaskType.WOKBEE.label, TaskType.WOKBEE.value)
    combo.currentIndexChanged.connect(form._on_type_changed)
    return combo

def build_text_page(form) -> QWidget:
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    form._content = QTextEdit()
    form._content.setPlaceholderText("文本正文")
    form._content.setFixedHeight(72)
    form._content.setStyleSheet(getattr(form, "_text_qss", ""))
    lay.addWidget(form._content)
    return page

def build_script_page(form) -> QWidget:
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    form._script_lang = QComboBox()
    form._script_lang.setFixedHeight(34)
    apply_combo_popup_style(form._script_lang, form.theme.colors, rounded=True)
    form._script_lang.addItem("Python", "python")
    form._script_lang.addItem("JavaScript (Node.js)", "javascript")
    form._script_lang.currentIndexChanged.connect(form._on_script_lang_changed)
    lay.addWidget(fld(form, "脚本语言", form._script_lang))
    form._code = QTextEdit()
    form._code.setPlaceholderText("Python 脚本代码（用系统 Python 执行）")
    form._code.setFixedHeight(72)
    form._code.setStyleSheet(getattr(form, "_text_qss", ""))
    lay.addWidget(form._code)
    return page

def build_wokbee_page(form) -> QWidget:
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    form._project_id = QLineEdit()
    form._project_id.setPlaceholderText("粘贴 WokBee 项目 ID（在项目列表右键「复制项目 ID」）")
    form._project_id.setFixedHeight(34)
    form._project_id.setStyleSheet(getattr(form, "_line_qss", ""))
    lay.addWidget(fld(form, "项目 ID", form._project_id))
    hint = QLabel("定时触发时将按该项目目标自动运行，无需额外填写指令。")
    hint.setWordWrap(True)
    hint.setStyleSheet(hint_label_qss(form.theme.colors))
    lay.addWidget(hint)
    return page
