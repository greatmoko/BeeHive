"""AI 配置 — 本机凭据库。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from beehive.common.ui.styles.checks import apply_checkbox
from beehive.common.ui.styles.inputs import apply_lineedit
from beehive.common.ui.styles.buttons import apply_primary_btn
from beehive.common.ui.styles.inputs import apply_textedit
from beehive.common.ui.styles.labels import style_hint_label
from beehive.common.ui.theme import Theme
from beehive.system.credentials.models import CredentialRecord
from beehive.system.credentials.credential_store import CredentialStore
from beehive.system.credentials.credential_store import CredentialVaultError
from beehive.common.ui.dialogs.message import show_tip as _tip, _confirm


from beehive.system.credentials.ui.card import _CredentialCard


from beehive.system.credentials.ui.editor import _CredentialDialog


class CredentialWorkspace(QWidget):
    def __init__(self, theme: Theme, store: CredentialStore | None = None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.store = store or CredentialStore()
        self._build()
        self.refresh()

    def _build(self):
        c = self.theme.colors
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setObjectName("credentialHeader")
        header.setStyleSheet(f"QFrame#credentialHeader {{ background: {c['content_bg']}; border: none; }}")
        hl = QVBoxLayout(header)
        hl.setContentsMargins(28, 20, 28, 12)
        title = QLabel("凭据库")
        title.setStyleSheet(
            f"font-size: 20px; font-weight: bold; color: {c['text']};"
            "background: transparent; border: none;"
        )
        hl.addWidget(title)
        tip = QLabel(
            "账号密码加密保存在本机（AES-256-GCM），主密钥放在 Windows 凭据管理器，"
            "不写入 config.json。同一 Windows 用户下的其它程序理论上仍可能读取；"
            "防的是磁盘被拷走或误提交。Agent 只能拿到环境变量名（如 WOKBEE_CRED_GOOGLE_PASSWORD），"
            "execute 时由本机注入明文；对话、时间线和命令展示都会隐藏账号密码。"
        )
        style_hint_label(tip, c)
        hl.addWidget(tip)
        root.addWidget(header)

        bar = QHBoxLayout()
        bar.setContentsMargins(28, 8, 28, 8)
        bar.addStretch()
        add_btn = QPushButton("添加凭据")
        apply_primary_btn(add_btn, c)
        add_btn.setMinimumWidth(96)
        add_btn.clicked.connect(self._on_add)
        bar.addWidget(add_btn)
        root.addLayout(bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self._list = QWidget()
        self._list_layout = QVBoxLayout(self._list)
        self._list_layout.setContentsMargins(28, 8, 28, 20)
        self._list_layout.setSpacing(8)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._list)
        root.addWidget(scroll, stretch=1)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        c = self.theme.colors
        try:
            records = self.store.list_records()
        except CredentialVaultError as e:
            err = QLabel(str(e))
            err.setWordWrap(True)
            err.setStyleSheet(f"color: {c['danger']}; padding: 20px;")
            self._list_layout.addWidget(err)
            return
        if not records:
            empty = QLabel("暂无凭据。点击「添加凭据」录入账号密码，供 Agent 登录其它系统。")
            empty.setWordWrap(True)
            empty.setStyleSheet(f"color: {c['text_hint']}; padding: 20px;")
            self._list_layout.addWidget(empty)
            return
        for rec in records:
            card = _CredentialCard(
                rec,
                self.theme,
                on_edit=self._on_edit,
                on_copy=self._on_copy,
                on_delete=self._on_delete,
            )
            self._list_layout.addWidget(card)

    def _on_add(self):
        dlg = _CredentialDialog(self.theme, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_from_dialog(dlg.get_data())

    def _on_edit(self, rec: CredentialRecord):
        dlg = _CredentialDialog(self.theme, rec, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_from_dialog(dlg.get_data())

    def _save_from_dialog(self, data: dict):
        try:
            self.store.upsert(**data)
        except CredentialVaultError as e:
            _tip(self, self.theme, str(e))
            return
        self.refresh()

    def _on_copy(self, rec: CredentialRecord):
        latest = self.store.get(rec.alias)
        pwd = latest.password if latest else rec.password
        clip = QApplication.clipboard()
        if clip is None:
            _tip(self, self.theme, "无法访问剪贴板")
            return
        clip.setText(pwd or "")
        _tip(self, self.theme, "已复制密码到剪贴板")

    def _on_delete(self, rec: CredentialRecord):
        if not _confirm(
            self,
            self.theme,
            "删除凭据",
            f"确定删除「{rec.title}」（{rec.alias}）？此操作不可恢复。",
        ):
            return
        try:
            self.store.delete(rec.id)
        except CredentialVaultError as e:
            _tip(self, self.theme, str(e))
            return
        self.refresh()
