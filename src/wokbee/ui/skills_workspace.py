"""AI 配置 — Skills 管理。"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QLineEdit, QDialog, QCheckBox, QFileDialog,
)

from tokbee.ui.styles.theme import Theme
from wokbee.core.settings import WokBeeSettings
from wokbee.core.skills_store import SkillsStore, SkillInfo, default_skills_root
from wokbee.ui.dialogs import open_path as _open_path, tip as _tip


def _downloads_dir() -> Path:
    """系统下载目录；取不到时回退到 ~/Downloads 或用户主目录。"""
    raw = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    if str(raw).strip():
        return Path(raw).expanduser()
    cand = Path.home() / "Downloads"
    if cand.is_dir():
        return cand
    return Path.home()


def _zip_skill(skill_path: Path, dest_zip: Path) -> bool:
    """把技能目录整体打成 zip（顶层含技能文件夹名）。"""
    try:
        with zipfile.ZipFile(str(dest_zip), "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(skill_path.rglob("*")):
                if p.is_file():
                    arc = p.relative_to(skill_path.parent).as_posix()
                    zf.write(str(p), arc)
        return True
    except OSError:
        return False


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

        from tokbee.ui.combo_style import checkbox_qss, secondary_btn_qss
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
            QPushButton:hover {{ background: #fff1f0; }}
            QPushButton:focus {{ outline: none; }}
        """)
        del_btn.clicked.connect(lambda: self.delete_clicked.emit(str(skill.path)))
        lay.addWidget(del_btn)

class SkillsWorkspace(QWidget):
    def __init__(self, theme: Theme, store: SkillsStore | None = None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.store = store or SkillsStore()
        self._settings = WokBeeSettings()
        self._build()
        self.refresh()

    def _build(self):
        c = self.theme.colors
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setStyleSheet(f"background: {c['content_bg']}; border: none;")
        hl = QVBoxLayout(header)
        hl.setContentsMargins(28, 20, 28, 12)
        title = QLabel("Skills")
        title.setStyleSheet(
            f"font-size: 20px; font-weight: bold; color: {c['text']};"
            "background: transparent; border: none;"
        )
        hl.addWidget(title)
        tip = QLabel(
            "技能以文件夹 + SKILL.md 形式存放。全局默认目录为 ~/.wokbee/skills（固定，不可修改），"
            "另可添加额外目录一并加载。启用后，WokBee 运行时把这些目录只读挂载（不复制到每个项目），"
            "Agent 可用 read_file/ls 读取，写操作需走审批。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(
            f"font-size: 12px; color: {c['text_hint']};"
            "background: transparent; border: none;"
        )
        hl.addWidget(tip)
        root.addWidget(header)

        from tokbee.ui.combo_style import rounded_lineedit_qss, secondary_btn_qss

        # 全局默认目录（只读）
        bar = QVBoxLayout()
        bar.setContentsMargins(28, 12, 28, 8)
        bar.setSpacing(6)
        default_row = QHBoxLayout()
        default_row.setSpacing(8)
        dlab = QLabel("默认目录")
        dlab.setStyleSheet(f"font-size: 11px; color: {c['text_hint']}; background: transparent; border: none;")
        default_row.addWidget(dlab)
        self._root_edit = QLineEdit(str(self.store.root))
        self._root_edit.setFixedHeight(32)
        self._root_edit.setReadOnly(True)
        self._root_edit.setStyleSheet(rounded_lineedit_qss(c))
        default_row.addWidget(self._root_edit, stretch=1)
        bar.addLayout(default_row)

        # 额外目录：添加 / 列表
        self._extras_box = QVBoxLayout()
        self._extras_box.setSpacing(4)
        add_dir_row = QHBoxLayout()
        add_dir_row.setSpacing(8)
        elab = QLabel("额外目录")
        elab.setStyleSheet(f"font-size: 11px; color: {c['text_hint']}; background: transparent; border: none;")
        add_dir_row.addWidget(elab)
        add_dir_btn = QPushButton("＋ 添加目录…")
        add_dir_btn.setFixedHeight(32)
        add_dir_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_dir_btn.setAutoDefault(False)
        add_dir_btn.setStyleSheet(secondary_btn_qss(c))
        add_dir_btn.clicked.connect(self._add_extra_dir)
        add_dir_row.addWidget(add_dir_btn)
        add_dir_row.addStretch()
        self._extras_box.addLayout(add_dir_row)
        bar.addLayout(self._extras_box)
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

    def refresh(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        self._render_extras()
        skills = self.store.list_skills()
        c = self.theme.colors
        if not skills:
            empty = QLabel("暂无 Skill。用「添加目录…」加载外部技能，或在 wokbee 页面点「生成SKILLS」。")
            empty.setStyleSheet(f"color: {c['text_hint']}; padding: 20px;")
            self._list_layout.addWidget(empty)
            return
        for s in skills:
            card = _SkillCard(s, self.theme)
            card.toggled.connect(self._on_toggle)
            card.open_clicked.connect(self._on_open)
            card.delete_clicked.connect(self._on_delete)
            card.share_clicked.connect(self._on_share)
            self._list_layout.addWidget(card)
        self._root_edit.setText(str(self.store.root))

    def _render_extras(self):
        # 清空已有额外目录行（保留第一行“添加目录”按钮）
        while self._extras_box.count() > 1:
            item = self._extras_box.takeAt(1)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        c = self.theme.colors
        for root_path in self.store.extra_roots():
            row = QHBoxLayout()
            row.setSpacing(8)
            lab = QLabel(str(root_path))
            lab.setWordWrap(False)
            lab.setStyleSheet(f"font-size: 12px; color: {c['text_secondary']}; background: transparent; border: none;")
            row.addWidget(lab, stretch=1)
            rm = QPushButton("移除")
            rm.setFixedHeight(26)
            rm.setCursor(Qt.CursorShape.PointingHandCursor)
            rm.setAutoDefault(False)
            from tokbee.ui.combo_style import secondary_btn_qss
            rm.setStyleSheet(secondary_btn_qss(c))
            rm.clicked.connect(lambda _=False, p=root_path: self._remove_extra_dir(p))
            row.addWidget(rm)
            self._extras_box.addLayout(row)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def _add_extra_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择要加载的 Skills 目录", str(self.store.root))
        if not path:
            return
        if self._settings.add_skills_dir(path):
            _tip(self, self.theme, f"已添加额外 Skills 目录：{path}")
        else:
            _tip(self, self.theme, "目录无效或已存在。")
        self.refresh()

    def _remove_extra_dir(self, path: Path):
        if self._settings.remove_skills_dir(path):
            _tip(self, self.theme, f"已移除额外 Skills 目录：{path}")
        else:
            _tip(self, self.theme, "移除失败或目录不存在。")
        self.refresh()

    def _on_toggle(self, folder: str, enabled: bool):
        self.store.set_enabled(folder, enabled)

    def _on_open(self, path: str):
        _open_path(Path(path))

    def _on_delete(self, path: str):
        c = self.theme.colors
        dlg = QDialog(self)
        dlg.setWindowTitle("删除 Skill")
        dlg.setFixedSize(380, 140)
        dlg.setStyleSheet(f"background: {c['content_bg']};")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.addWidget(QLabel(f"确定删除技能「{Path(path).name}」？此操作不可恢复。"))
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(dlg.reject)
        ok = QPushButton("删除")
        ok.clicked.connect(dlg.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.store.delete_path(path)
            self.refresh()

    def _on_share(self, path: str):
        skill_dir = Path(path)
        if not skill_dir.is_dir():
            _tip(self, self.theme, "技能目录不存在。")
            return
        downloads = _downloads_dir()
        try:
            downloads.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            _tip(self, self.theme, f"无法访问下载目录：{e}")
            return
        zip_path = downloads / f"{skill_dir.name}.zip"
        n = 1
        while zip_path.exists():
            zip_path = downloads / f"{skill_dir.name}-{n}.zip"
            n += 1
        if _zip_skill(skill_dir, zip_path):
            _tip(self, self.theme, f"已打包到：{zip_path}")
            _open_path(downloads)
        else:
            _tip(self, self.theme, "打包失败。")
