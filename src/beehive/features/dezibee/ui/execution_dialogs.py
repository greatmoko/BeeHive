"""执行审批与澄清弹窗：决定始终回传发起请求的线程。"""
from __future__ import annotations
import json
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout
from beehive.common.ui.styles import rounded_textedit_qss
from beehive.common.ui.styles.buttons import apply_danger_btn, apply_primary_btn
from beehive.features.wokbee.ui.ask_user_dialog import AskUserDialog

def on_approval_needed(view, worker, pending: object):
    from shiboken6 import isValid

    # 捕获发起审批的 worker，弹窗期间切换需求也不能把决定发给别的任务。
    if worker is None or not worker.isRunning():
        return
    items = pending if isinstance(pending, list) else []
    if not items:
        return
    dlg = QDialog(view.window() or view)
    dlg.setWindowTitle(f"DeziBee 操作审批 · {worker._req.title}（{worker._req.id}）")
    dlg.resize(640, 460)
    dlg.setStyleSheet(f"background: {view.theme.colors['content_bg']}; color: {view.theme.colors['text']};")
    layout = QVBoxLayout(dlg)
    layout.addWidget(QLabel("以下工具调用尚未执行，请检查操作及完整参数："))
    details = QTextEdit()
    details.setReadOnly(True)
    details.setStyleSheet(rounded_textedit_qss(view.theme.colors))
    details.setPlainText(json.dumps(items, ensure_ascii=False, indent=2, default=str))
    layout.addWidget(details)
    row = QHBoxLayout()
    row.addStretch()
    reject = QPushButton("全部拒绝")
    approve = QPushButton("全部批准")
    apply_danger_btn(reject, view.theme.colors)
    apply_primary_btn(approve, view.theme.colors)
    approve.setAutoDefault(False)
    reject.setDefault(True)
    reject.clicked.connect(dlg.reject)
    approve.clicked.connect(dlg.accept)
    row.addWidget(reject)
    row.addWidget(approve)
    layout.addLayout(row)
    # 取消或审批超时导致 worker 退出时，关闭失效弹窗。
    worker.finished.connect(dlg.reject)
    accepted = dlg.exec() == QDialog.DialogCode.Accepted
    if isValid(worker):
        worker.finished.disconnect(dlg.reject)
    dlg.deleteLater()
    if isValid(worker) and worker.isRunning():
        decision = {"type": "approve"} if accepted else {"type": "reject", "message": "用户拒绝"}
        worker.resolve_approval([dict(decision) for _ in items])

def on_ask_user_needed(view, worker, payload: object):
    """主线程弹窗收集澄清答案，回传给发出请求的那个 worker。"""
    from shiboken6 import isValid

    if worker is None or not worker.isRunning():
        return
    data = payload if isinstance(payload, dict) else {"type": "ask_user", "questions": []}
    dlg = AskUserDialog(data, view.theme, parent=view.window() or view)
    worker.finished.connect(dlg.reject)
    accepted = dlg.exec() == AskUserDialog.DialogCode.Accepted
    answers = dlg.result_payload() if accepted else {"cancelled": True}
    if isValid(worker):
        worker.finished.disconnect(dlg.reject)
    dlg.deleteLater()
    if isValid(worker) and worker.isRunning():
        worker.resolve_ask_user(answers)
