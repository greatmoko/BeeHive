"""自然语言输入与任务操作按钮布局。"""
from __future__ import annotations
from PySide6.QtWidgets import QHBoxLayout, QTextEdit, QPushButton
from beehive.common.ui.widgets.model_selector import ModelSelectorButton
from beehive.common.ui.styles.buttons import apply_primary_btn, apply_secondary_btn, apply_danger_btn

def build_action_fields(form, outer):
    c = form.theme.colors
    # ① 自然语言描述（单独一行）
    form._nl_input = QTextEdit()
    form._nl_input.setPlaceholderText(
        "用自然语言描述定时任务，如：每天上午9点给企业微信群推送问候"
    )
    form._nl_input.setFixedHeight(52)
    form._nl_input.setStyleSheet(form._text_qss)
    form._nl_input.textChanged.connect(form.owner.actions._update_action_btns)
    outer.addWidget(form._nl_input)

    # ② 操作栏：生成模型 + AI 生成 + 保存 + 删除 + 立即运行
    ai_row = QHBoxLayout()
    ai_row.setSpacing(8)
    form._gen_combo = ModelSelectorButton(
        form.theme, width=300,
        placeholder="（暂无可用模型）", hint_tooltip="AI 生成所用模型",
    )
    ai_row.addWidget(form._gen_combo)

    form._gen_btn = QPushButton("AI 生成")
    apply_secondary_btn(form._gen_btn, c)
    form._gen_btn.setFixedSize(80, 34)
    form._gen_btn.clicked.connect(form.owner.generation._on_nl_generate)
    ai_row.addWidget(form._gen_btn)

    form._save_btn = QPushButton("保存")
    apply_primary_btn(form._save_btn, c)
    form._save_btn.setFixedSize(64, 34)
    form._save_btn.clicked.connect(form.owner.actions._on_save)
    ai_row.addWidget(form._save_btn)

    form._del_btn = QPushButton("删除")
    apply_danger_btn(form._del_btn, c)
    form._del_btn.setFixedSize(64, 34)
    form._del_btn.clicked.connect(form.owner.actions._on_delete)
    ai_row.addWidget(form._del_btn)

    form._run_btn = QPushButton("立即运行")
    apply_secondary_btn(form._run_btn, c)
    form._run_btn.setFixedSize(80, 34)
    form._run_btn.clicked.connect(form.owner.actions._on_run_now)
    ai_row.addWidget(form._run_btn)
    outer.addLayout(ai_row)
