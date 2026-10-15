"""新建需求名称输入。"""
from __future__ import annotations
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout
from beehive.common.ui.styles.buttons import apply_primary_btn, apply_secondary_btn
from beehive.common.ui.styles.inputs import apply_lineedit
from beehive.common.ui.theme import Theme

class NewReqDialog(QDialog):
    """新建需求对话框：只需输入需求名称，回车即创建。"""

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        c = theme.colors
        self.setWindowTitle("新建需求")
        self.setMinimumWidth(460)
        self.setStyleSheet(f"background: {c['content_bg']};")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(10)

        title_lbl = QLabel("需求名称")
        title_lbl.setStyleSheet(f"font-size: 13px; color: {c['text']};")
        layout.addWidget(title_lbl)
        self._title = QLineEdit()
        self._title.setPlaceholderText("例如：借款首页（详细需求可在发送消息时描述）")
        apply_lineedit(self._title, c)
        layout.addWidget(self._title)

        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("取消")
        cancel.setFixedSize(80, 34)
        apply_secondary_btn(cancel, c)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        ok = QPushButton("创建需求")
        ok.setFixedSize(100, 34)
        apply_primary_btn(ok, c)
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        layout.addLayout(row)

        # 回车即创建；名称为空时创建按钮禁用，避免误退出
        self._title.returnPressed.connect(ok.click)
        self._title.textChanged.connect(lambda t: ok.setEnabled(bool(t.strip())))
        ok.setEnabled(False)

    def result_data(self) -> str:
        return self._title.text().strip()
