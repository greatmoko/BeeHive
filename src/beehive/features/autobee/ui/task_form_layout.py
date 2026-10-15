"""任务表单布局装配。"""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QScrollArea, QStackedWidget, QCheckBox, QFrame
from beehive.common.ui.widgets.model_selector import ModelSelectorButton
from beehive.features.autobee.core.models import TaskType

from beehive.common.ui.styles.inputs import rounded_lineedit_qss, rounded_textedit_qss
from beehive.features.autobee.ui.task_action_fields import build_action_fields
from beehive.features.autobee.ui import task_type_fields as fields

def build_task_form(form) -> QWidget:
    page = QWidget()
    c = form.theme.colors
    page.setStyleSheet(f"background: {c['content_bg']};")
    form_lay = QVBoxLayout(page)
    form_lay.setContentsMargins(0, 0, 0, 0)
    form_lay.setSpacing(0)

    # 上半：可滚动表单区（避免矮窗口下推送地址被挡住）
    body = QWidget()
    body.setStyleSheet(f"background: {c['content_bg']};")
    outer = QVBoxLayout(body)
    outer.setContentsMargins(20, 16, 20, 8)
    outer.setSpacing(10)

    # 统一输入样式
    form._line_qss = rounded_lineedit_qss(c)
    form._text_qss = rounded_textedit_qss(c)
    build_action_fields(form, outer)

    # ③ 任务名称 | 定时 cron
    row2 = QHBoxLayout()
    row2.setSpacing(12)
    form._name = QLineEdit()
    form._name.setPlaceholderText("定时任务名称")
    form._name.setFixedHeight(34)
    form._name.setStyleSheet(form._line_qss)
    row2.addWidget(fields.fld(form, "任务名称", form._name), 1)

    form._schedule = QLineEdit()
    form._schedule.setPlaceholderText("cron，如 0 9 * * *")
    form._schedule.setFixedHeight(34)
    form._schedule.setStyleSheet(form._line_qss)
    form._schedule.textChanged.connect(form._update_cron_preview)
    cron_fld, form._cron_title = fields.fld_with_label(form, "定时 (cron)", form._schedule)
    form._cron_text_value = ""
    row2.addWidget(cron_fld, 1)
    outer.addLayout(row2)

    # ④ 执行模型 + 任务类型
    row3 = QHBoxLayout()
    row3.setSpacing(12)
    form._exec_combo = ModelSelectorButton(
        form.theme, width=300,
        placeholder="（暂无可用模型）", hint_tooltip="执行模型",
    )
    row3.addWidget(fields.fld(form, "执行模型", form._exec_combo), 1)
    form._type_combo = fields.build_type_combo(form)
    row3.addWidget(fields.fld(form, "任务类型", form._type_combo), 1)
    outer.addLayout(row3)
    form._refill_model_combos()

    # 类型专属配置
    form._config_stack = QStackedWidget()
    form._config_stack.addWidget(fields.build_text_page(form))
    form._config_stack.addWidget(fields.build_script_page(form))
    form._config_stack.addWidget(fields.build_wokbee_page(form))
    outer.addWidget(form._config_stack)

    # ⑤ 微信推送地址（有值即开启推送）
    form._webhook = QLineEdit()
    form._webhook.setPlaceholderText("企业微信群机器人 Webhook 地址（填写即开启推送）")
    form._webhook.setFixedHeight(34)
    form._webhook.setStyleSheet(form._line_qss)
    outer.addWidget(fields.fld(form, "微信推送地址", form._webhook))
    form._notify_wechat = QCheckBox("任务完成后，通过微信消息网关通知我（仅发送给绑定的微信账号）")
    form._notify_wechat.setStyleSheet(
        f"font-size: 12px; color: {c['text_secondary']}; background: transparent;"
    )
    form._update_wechat_notify_state()
    outer.addWidget(form._notify_wechat)

    outer.addWidget(form.owner.history)
    outer.addStretch(1)

    # 默认任务类型：WokBee
    form._set_task_type(TaskType.WOKBEE)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
    scroll.setWidget(body)
    form_lay.addWidget(scroll, stretch=1)

    return page
