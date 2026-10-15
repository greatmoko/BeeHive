"""项目命名与审批业务对话框。"""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextEdit, QVBoxLayout, QWidget
from beehive.common.ui.theme import Theme
from beehive.common.ui.styles.menus import bind_text_edit_context_menu
from datetime import datetime
from PySide6.QtWidgets import QCheckBox
from beehive.common.ui.styles import apply_primary_btn, apply_secondary_btn
from beehive.common.ui.styles.checks import checkbox_qss
from beehive.features.wokbee.projects.models import ApprovalFlags, MAX_PROJECT_TITLE_LEN

def build_approval_checkboxes(
    theme: Theme,
    parent: QWidget | None = None,
) -> tuple[QWidget, dict[str, QCheckBox]]:
    """创建四个审核勾选控件，返回容器与 checkbox 字典。"""
    c = theme.colors
    box = QWidget(parent)
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)

    defs = [
        ("skip_read", "读免审", "读操作免审"),
        ("skip_write", "写免审", "写操作免审"),
        ("skip_routine", "常规操作免审", "常规操作免审"),
        ("skip_high_risk", "高危操作免审", "本机命令、挂载外目录、读取凭据密码"),
        ("bypass_sandbox", "忽略沙箱限制", "解除全部人工审批，execute 可操作任意真实路径（含项目外目录）"),
    ]
    checks: dict[str, QCheckBox] = {}
    cb_qss = checkbox_qss(c)
    for key, label, tip in defs:
        cb = QCheckBox(label)
        cb.setToolTip(tip)
        cb.setStyleSheet(cb_qss)
        lay.addWidget(cb)
        checks[key] = cb

    return box, checks


def apply_flags_to_checks(checks: dict[str, QCheckBox], flags: ApprovalFlags) -> None:
    checks["skip_read"].setChecked(flags.skip_read)
    checks["skip_write"].setChecked(flags.skip_write)
    checks["skip_routine"].setChecked(flags.skip_routine)
    checks["skip_high_risk"].setChecked(flags.skip_high_risk)
    checks["bypass_sandbox"].setChecked(flags.bypass_sandbox)


def flags_from_checks(checks: dict[str, QCheckBox]) -> ApprovalFlags:
    return ApprovalFlags(
        skip_read=checks["skip_read"].isChecked(),
        skip_write=checks["skip_write"].isChecked(),
        skip_routine=checks["skip_routine"].isChecked(),
        skip_high_risk=checks["skip_high_risk"].isChecked(),
        bypass_sandbox=checks["bypass_sandbox"].isChecked(),
    )


def _title_from_goal(goal: str, max_len: int = MAX_PROJECT_TITLE_LEN) -> str:
    s = " ".join((goal or "").split())
    if not s:
        return "未命名项目"
    if len(s) <= max_len:
        return s
    return s[:max_len] + "…"


def _default_project_title(when: datetime | None = None) -> str:
    """默认名称：项目 + 创建时间（不超过名称上限）。"""
    dt = when or datetime.now()
    raw = f"项目{dt.strftime('%m-%d %H:%M')}"
    return raw[:MAX_PROJECT_TITLE_LEN]


def _prompt_approval_flags(
    parent: QWidget,
    theme: Theme,
    current: ApprovalFlags,
    title: str = "项目审核策略",
) -> ApprovalFlags | None:
    """弹窗编辑四个审核勾选项。"""
    c = theme.colors
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setFixedSize(420, 320)
    dlg.setStyleSheet(f"background: {c['content_bg']};")
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 20, 24, 18)
    layout.setSpacing(10)

    tip = QLabel(
        "勾选表示该级别免审；未勾选则执行时需要人工审批。仅影响当前项目。"
    )
    tip.setWordWrap(True)
    tip.setStyleSheet(f"font-size: 12px; color: {c['text_hint']};")
    layout.addWidget(tip)

    box, checks = build_approval_checkboxes(theme)
    apply_flags_to_checks(checks, current)
    layout.addWidget(box)
    layout.addStretch()

    row = QHBoxLayout()
    row.addStretch()
    cancel = QPushButton("取消")
    cancel.setFixedWidth(72)
    apply_secondary_btn(cancel, c)
    cancel.clicked.connect(dlg.reject)
    ok = QPushButton("保存")
    ok.setFixedWidth(72)
    apply_primary_btn(ok, c)
    ok.clicked.connect(dlg.accept)
    row.addWidget(cancel)
    row.addWidget(ok)
    layout.addLayout(row)

    if dlg.exec() == QDialog.DialogCode.Accepted:
        return flags_from_checks(checks)
    return None
