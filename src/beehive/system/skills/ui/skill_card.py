"""AI 配置 — Skills 管理。"""

from __future__ import annotations


from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QCheckBox

from beehive.common.ui.theme import Theme
from beehive.system.skills.manifest import SkillInfo


class _SkillCard(QFrame):
    toggled = Signal(str, bool)
    open_clicked = Signal(str)
    delete_clicked = Signal(str)
    share_clicked = Signal(str)

    def __init__(self, skill: SkillInfo, theme: Theme, parent=None):
        super().__init__(parent)
        self.skill = skill
        self.theme = theme
        c = theme.colors
        self.setObjectName("skillCard")
        self.setStyleSheet(f"""
            QFrame#skillCard {{
                background: {c["card_bg"]};
                border: 1px solid {c["border_light"]};
                border-radius: 8px;
            }}
            QFrame#skillCard QLabel, QFrame#skillCard QCheckBox {{
                background: transparent; border: none;
            }}
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(10)

        info = QVBoxLayout()
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title = QLabel(skill.name)
        title.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {c['text']}; background: transparent; border: none;")
        title_row.addWidget(title)
        local = skill.path.name.startswith("wokbee-")
        badge = QLabel("本地生成" if local else "外部")
        badge.setStyleSheet(f"""
            background: {c["accent_light"] if local else c["tag_bg"]};
            color: {c["accent"] if local else c["text_secondary"]};
            border-radius: 4px; padding: 1px 6px; font-size: 10px;
        """)
        title_row.addWidget(badge)
        title_row.addStretch()
        info.addLayout(title_row)
        desc = QLabel(skill.description or skill.path.name)
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: 12px; color: {c['text_secondary']}; background: transparent; border: none;")
        info.addWidget(desc)
        path = QLabel(str(skill.path))
        path.setStyleSheet(f"font-size: 11px; color: {c['text_hint']}; background: transparent; border: none;")
        info.addWidget(path)
        lay.addLayout(info, stretch=1)

        from beehive.common.ui.styles.checks import checkbox_qss
        from beehive.common.ui.styles.buttons import secondary_btn_qss
        self._chk = QCheckBox("启用")
        self._chk.setChecked(skill.enabled)
        self._chk.setStyleSheet(checkbox_qss(c))
        self._chk.toggled.connect(lambda v: self.toggled.emit(skill.path.name, v))
        lay.addWidget(self._chk)

        open_btn = QPushButton("打开")
        open_btn.setFixedHeight(30)
        open_btn.setMinimumWidth(56)
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.setAutoDefault(False)
        open_btn.setStyleSheet(secondary_btn_qss(c))
        open_btn.clicked.connect(lambda: self.open_clicked.emit(str(skill.path)))
        lay.addWidget(open_btn)

        share_btn = QPushButton("分享")
        share_btn.setFixedHeight(30)
        share_btn.setMinimumWidth(56)
        share_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        share_btn.setAutoDefault(False)
        share_btn.setStyleSheet(secondary_btn_qss(c))
        share_btn.clicked.connect(lambda: self.share_clicked.emit(str(skill.path)))
        lay.addWidget(share_btn)

        del_btn = QPushButton("删除")
        del_btn.setFixedHeight(30)
        del_btn.setMinimumWidth(56)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setAutoDefault(False)
        del_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {c["danger"]};
                border: 1px solid {c["border"]}; border-radius: 6px;
                padding: 0 10px; text-decoration: none;
            }}
            QPushButton:hover {{ background: {c["danger_light"]}; }}
            QPushButton:focus {{ outline: none; }}
        """)
        del_btn.clicked.connect(lambda: self.delete_clicked.emit(str(skill.path)))
        lay.addWidget(del_btn)
