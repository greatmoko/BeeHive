"""按职责组织的 card。"""
from __future__ import annotations
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from beehive.common.ui.styles.buttons import apply_danger_btn
from beehive.common.ui.styles.buttons import apply_secondary_btn
from beehive.common.ui.theme import Theme
from beehive.system.credentials.models import CredentialRecord


class _CredentialCard(QFrame):
    def __init__(
        self,
        rec: CredentialRecord,
        theme: Theme,
        *,
        on_edit,
        on_copy,
        on_delete,
        parent=None,
    ):
        super().__init__(parent)
        self.rec = rec
        c = theme.colors
        self.setObjectName("credentialCard")
        self.setStyleSheet(f"""
            QFrame#credentialCard {{
                background: {c["card_bg"]};
                border: 1px solid {c["border_light"]};
                border-radius: 8px;
            }}
            QFrame#credentialCard QLabel {{
                background: transparent; border: none;
            }}
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(10)

        info = QVBoxLayout()
        title = QLabel(rec.title or rec.alias)
        title.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {c['text']};"
            "background: transparent; border: none;"
        )
        info.addWidget(title)
        bits = [f"别名 {rec.alias}"]
        if rec.username:
            bits.append(rec.username)
        if rec.url:
            bits.append(rec.url)
        sub = QLabel(" · ".join(bits))
        sub.setWordWrap(True)
        sub.setStyleSheet(
            f"font-size: 12px; color: {c['text_secondary']};"
            "background: transparent; border: none;"
        )
        info.addWidget(sub)
        lay.addLayout(info, stretch=1)

        copy_btn = QPushButton("复制密码")
        apply_secondary_btn(copy_btn, c, height=30)
        copy_btn.setMinimumWidth(84)
        copy_btn.clicked.connect(lambda: on_copy(rec))
        lay.addWidget(copy_btn)

        edit_btn = QPushButton("编辑")
        apply_secondary_btn(edit_btn, c, height=30)
        edit_btn.setMinimumWidth(56)
        edit_btn.clicked.connect(lambda: on_edit(rec))
        lay.addWidget(edit_btn)

        del_btn = QPushButton("删除")
        apply_danger_btn(del_btn, c, height=30)
        del_btn.setMinimumWidth(56)
        del_btn.clicked.connect(lambda: on_delete(rec))
        lay.addWidget(del_btn)
