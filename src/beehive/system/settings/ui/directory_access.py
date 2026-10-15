"""全局授权目录管理；目录选择与移除仍即时保存。"""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QFileDialog
from beehive.common.ui.styles.buttons import secondary_btn_qss
from beehive.common.ui.styles.labels import hint_label_qss
from beehive.system.settings.ui.form_helpers import section_label
from beehive.common.ui.dialogs.message import show_tip as _tip


class DirectoryAccessForm(QWidget):
    def __init__(self, theme, settings, parent=None):
        super().__init__(parent)
        self.theme, self.settings = theme, settings
        c = theme.colors
        bl = QVBoxLayout(self)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(18)
        bl.addWidget(section_label("已授权附加目录（项目外）", self.theme.colors))
        ext_intro = QLabel(
            "列表中的目录已加入全局白名单，Agent 可经 /ext/<slug>/… 虚拟路径用文件工具访问。"
            "运行中也可让 Agent 调 request_access 申请新目录（走人工高危审批，获批后自动写入此列表）。"
        )
        ext_intro.setWordWrap(True)
        ext_intro.setStyleSheet(hint_label_qss(c))
        bl.addWidget(ext_intro)
        self._ext_box = QFrame()
        self._ext_box.setStyleSheet(
            f"QFrame {{ background: {c['content_bg']}; border: none; border-radius: 6px; }}"
        )
        self._ext_lay = QVBoxLayout(self._ext_box)
        self._ext_lay.setContentsMargins(12, 10, 12, 10)
        self._ext_lay.setSpacing(6)
        bl.addWidget(self._ext_box)
        ext_add = QPushButton("添加目录…")
        ext_add.setFixedHeight(34)
        ext_add.setCursor(Qt.CursorShape.PointingHandCursor)
        ext_add.setStyleSheet(secondary_btn_qss(self.theme.colors))
        ext_add.clicked.connect(self._add_ext_dir)
        bl.addWidget(ext_add, alignment=Qt.AlignmentFlag.AlignLeft)
        ext_note = QLabel(
            "移除仅撤销该目录的访问权；需要时可在运行中由 Agent 重新申请，或在此重新添加。"
        )
        ext_note.setWordWrap(True)
        ext_note.setStyleSheet(hint_label_qss(c))
        bl.addWidget(ext_note)


    def _reload_ext_dirs(self):
        """刷新「已授权附加目录」列表。"""
        while self._ext_lay.count():
            item = self._ext_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        entries = self.settings.additional_directories
        if not entries:
            empty = QLabel("（未添加任何附加目录）")
            empty.setStyleSheet(
                f"font-size: 12px; color: {self.theme.colors['text_hint']};"
                "background: transparent; border: none;"
            )
            self._ext_lay.addWidget(empty)
            return
        for entry in entries:
            self._ext_lay.addWidget(self._ext_row(entry["name"], entry["path"]))

    def _ext_row(self, name: str, path: str) -> QWidget:
        c = self.theme.colors
        row = QFrame()
        row.setStyleSheet("QFrame { background: transparent; border: none; }")
        row.setFixedHeight(36)
        h = QHBoxLayout(row)
        h.setContentsMargins(4, 2, 4, 2)
        h.setSpacing(8)
        lbl = QLabel(f"{name}  ·  {path}")
        lbl.setStyleSheet(
            f"font-size: 12px; color: {c['text']}; background: transparent; border: none;"
        )
        lbl.setToolTip(path)
        h.addWidget(lbl, stretch=1)
        rm = QPushButton("移除")
        rm.setFixedSize(56, 26)
        rm.setCursor(Qt.CursorShape.PointingHandCursor)
        rm.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_bg"]}; color: {c["text"]};
                border: none; border-radius: 5px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {c["btn_hover"]}; }}
        """)
        rm.clicked.connect(lambda _=False, p=path: self._remove_ext_dir(p))
        h.addWidget(rm, alignment=Qt.AlignmentFlag.AlignRight)
        return row

    def _add_ext_dir(self):
        start = str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "选择要加入白名单的目录", start)
        if not path:
            return
        res = self.settings.add_additional_directory(Path(path).name, path)
        if res is None:
            _tip(self, self.theme, "该目录不存在或无权限，无法加入白名单。")
            return
        self._reload_ext_dirs()
        _tip(
            self,
            self.theme,
            f"已加入白名单：{res['path']}\nAgent 将可用 /ext/… 虚拟路径用文件工具访问。",
        )

    def _remove_ext_dir(self, path: str):
        if self.settings.remove_additional_directory(path):
            self._reload_ext_dirs()
        else:
            _tip(self, self.theme, "移除失败：该路径不在白名单中。")
