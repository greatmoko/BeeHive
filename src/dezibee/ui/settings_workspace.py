"""DeziBee 设置页（挂在 AI 配置二级导航）：需求工作文件夹。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from tokbee.ui.combo_style import (
    hint_label_qss,
    rounded_lineedit_qss,
    secondary_btn_qss,
    section_label_qss,
)
from tokbee.ui.styles.theme import Theme

from wokbee.core.settings import WokBeeSettings
from wokbee.ui.dialogs import tip as _tip

from dezibee.core.store import DeziBeeStore


class DeziBeeSettingsWorkspace(QWidget):
    """DeziBee 设置：需求工作文件夹。"""

    def __init__(
        self,
        theme: Theme,
        settings: WokBeeSettings | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.theme = theme
        self.settings = settings or WokBeeSettings()
        self._build()
        self._load()

    def _build(self):
        c = self.theme.colors
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setStyleSheet(f"background: {c['content_bg']}; border: none;")
        hl = QVBoxLayout(header)
        hl.setContentsMargins(28, 20, 28, 12)
        title = QLabel("DeziBee 设置")
        title.setStyleSheet(
            f"font-size: 20px; font-weight: bold; color: {c['text']};"
            "background: transparent; border: none;"
        )
        hl.addWidget(title)
        intro = QLabel("配置 DeziBee（AI 产品设计）的需求工作文件夹。")
        intro.setWordWrap(True)
        intro.setStyleSheet(hint_label_qss(c))
        hl.addWidget(intro)
        root.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(28, 16, 28, 16)
        bl.setSpacing(18)

        bl.addWidget(self._section_label("需求工作文件夹"))
        row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setFixedHeight(34)
        self._path_edit.setPlaceholderText("选择 DeziBee 工作文件夹…")
        self._path_edit.setStyleSheet(rounded_lineedit_qss(c))
        row.addWidget(self._path_edit, stretch=1)
        browse = QPushButton("选择文件夹")
        browse.setFixedSize(92, 34)
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        browse.setStyleSheet(secondary_btn_qss(c))
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        bl.addLayout(row)

        hint = QLabel(
            "每个需求在其下创建「需求ID」子目录，Demo / PRD / 素材全部存放在其中；"
            "路径不存在时自动创建。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(hint_label_qss(c))
        bl.addWidget(hint)

        self._stat = QLabel()
        self._stat.setWordWrap(True)
        self._stat.setStyleSheet(hint_label_qss(c))
        bl.addWidget(self._stat)

        bl.addWidget(self._section_label("终端运行软件"))
        self._terminal_combo = QComboBox()
        self._terminal_combo.setFixedHeight(34)
        self._terminal_combo.setFixedWidth(300)
        self._terminal_combo.setStyleSheet(rounded_lineedit_qss(c))
        bl.addWidget(self._terminal_combo, alignment=Qt.AlignmentFlag.AlignLeft)
        terminal_hint = QLabel(
            "Agent 运行命令行时使用的终端；未选择时默认使用 cmd。"
        )
        terminal_hint.setWordWrap(True)
        terminal_hint.setStyleSheet(hint_label_qss(c))
        bl.addWidget(terminal_hint)

        bl.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(28, 10, 28, 16)
        btn_bar.addStretch()
        save_btn = QPushButton("保存")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setFixedHeight(34)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_primary"]}; color: white;
                border: none; border-radius: 6px; padding: 0 18px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {c["btn_primary_hover"]}; }}
        """)
        save_btn.clicked.connect(self._on_save)
        btn_bar.addWidget(save_btn)
        root.addLayout(btn_bar)

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(section_label_qss(self.theme.colors))
        return lbl

    def _browse(self):
        start = self._path_edit.text().strip() or str(Path.home() / "WokBee" / "DeziBee")
        path = QFileDialog.getExistingDirectory(self, "选择 DeziBee 工作文件夹", start)
        if path:
            self._path_edit.setText(path)
            self._update_stat()

    def _update_stat(self):
        """统计当前路径下的需求数：让用户确认选对了文件夹。"""
        raw = self._path_edit.text().strip()
        if not raw:
            self._stat.setText("")
            return
        path = Path(raw).expanduser()
        if not path.exists():
            self._stat.setText("该文件夹尚不存在，保存时自动创建。")
            return
        try:
            count = len(DeziBeeStore(work_root=path).list_ids())
        except OSError as e:
            self._stat.setText(f"无法读取该文件夹：{e}")
            return
        self._stat.setText(f"当前文件夹下已有 {count} 个需求。")

    def _load(self):
        self._path_edit.setText(str(self.settings.dezibee_work_root))
        self._update_stat()
        self._reload_terminals()

    def _reload_terminals(self):
        """填充终端下拉框：系统内可用命令行工具 + 未选中时默认 cmd。"""
        from wokbee.core.settings import detect_terminal_apps

        self._terminal_combo.blockSignals(True)
        self._terminal_combo.clear()
        terminals = detect_terminal_apps()
        for key, exe in terminals:
            self._terminal_combo.addItem(exe, key)
        current = self.settings.terminal_app
        idx = 0
        for i, (key, _exe) in enumerate(terminals):
            if key == current:
                idx = i
                break
        self._terminal_combo.setCurrentIndex(idx)
        self._terminal_combo.blockSignals(False)

    def showEvent(self, event):
        super().showEvent(event)
        self._load()

    def _on_save(self):
        raw = self._path_edit.text().strip()
        if not raw:
            _tip(self, self.theme, "请设置 DeziBee 需求工作文件夹。")
            return
        path = Path(raw).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            _tip(self, self.theme, f"无法创建 DeziBee 工作文件夹：{e}")
            return
        self.settings.dezibee_work_root = path
        self.settings.terminal_app = str(self._terminal_combo.currentData() or "cmd")
        self.settings.save()
        self._path_edit.setText(str(path))
        self._update_stat()
        _tip(self, self.theme, "DeziBee 设置已保存。")
