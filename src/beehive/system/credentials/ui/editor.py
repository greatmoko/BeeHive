"""按职责组织的 editor。"""
from __future__ import annotations
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextEdit, QVBoxLayout
from beehive.common.ui.styles.checks import apply_checkbox
from beehive.common.ui.styles.inputs import apply_lineedit
from beehive.common.ui.styles.buttons import apply_primary_btn
from beehive.common.ui.styles.buttons import apply_secondary_btn
from beehive.common.ui.styles.inputs import apply_textedit
from beehive.common.ui.theme import Theme
from beehive.system.credentials.models import CredentialRecord


class _CredentialDialog(QDialog):
    def __init__(self, theme: Theme, rec: CredentialRecord | None = None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._rec = rec
        editing = rec is not None
        self.setWindowTitle("编辑凭据" if editing else "添加凭据")
        self.setMinimumSize(460, 460)
        c = theme.colors
        self.setStyleSheet(f"background: {c['content_bg']};")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(10)

        def _label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"font-size: 13px; font-weight: 600; color: {c['text']};"
                "background: transparent; border: none;"
            )
            return lbl

        lay.addWidget(_label("别名（Agent 检索用，唯一）"))
        self._alias = QLineEdit()
        self._alias.setPlaceholderText("例如 gitlab 或 company-oa")
        apply_lineedit(self._alias, c)
        lay.addWidget(self._alias)

        lay.addWidget(_label("标题"))
        self._title = QLineEdit()
        self._title.setPlaceholderText("显示名称，可与别名相同")
        apply_lineedit(self._title, c)
        lay.addWidget(self._title)

        lay.addWidget(_label("网址"))
        self._url = QLineEdit()
        self._url.setPlaceholderText("https://…（可选）")
        apply_lineedit(self._url, c)
        lay.addWidget(self._url)

        lay.addWidget(_label("用户名 / 账号"))
        self._user = QLineEdit()
        apply_lineedit(self._user, c)
        lay.addWidget(self._user)

        lay.addWidget(_label("密码"))
        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        if editing:
            self._password.setPlaceholderText("留空则不修改原密码")
        apply_lineedit(self._password, c)
        lay.addWidget(self._password)

        self._show_pwd = QCheckBox("显示密码")
        apply_checkbox(self._show_pwd, c)
        self._show_pwd.toggled.connect(self._toggle_password)
        lay.addWidget(self._show_pwd)

        lay.addWidget(_label("备注（可选）"))
        self._notes = QTextEdit()
        self._notes.setFixedHeight(80)
        apply_textedit(self._notes, c)
        lay.addWidget(self._notes)

        if rec:
            self._alias.setText(rec.alias)
            self._title.setText(rec.title)
            self._url.setText(rec.url)
            self._user.setText(rec.username)
            self._notes.setPlainText(rec.notes)

        lay.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("取消")
        apply_secondary_btn(cancel, c)
        cancel.setMinimumWidth(80)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        save = QPushButton("保存")
        apply_primary_btn(save, c)
        save.setMinimumWidth(80)
        save.clicked.connect(self._on_save)
        row.addWidget(save)
        lay.addLayout(row)

    def _toggle_password(self, on: bool):
        mode = QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
        self._password.setEchoMode(mode)

    def _on_save(self):
        if not self._alias.text().strip():
            self._alias.setFocus()
            return
        self.accept()

    def get_data(self) -> dict:
        return {
            "rec_id": self._rec.id if self._rec else "",
            "alias": self._alias.text().strip(),
            "title": self._title.text().strip(),
            "url": self._url.text().strip(),
            "username": self._user.text().strip(),
            "password": self._password.text(),
            "notes": self._notes.toPlainText(),
        }
