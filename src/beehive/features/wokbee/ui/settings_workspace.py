"""WokBee 专属项目设置页面，系统运行设置在独立系统页面。"""
from pathlib import Path
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
from beehive.common.ui.dialogs.message import show_tip as _tip
from PySide6.QtWidgets import QScrollArea
from beehive.common.ui.theme import Theme
from beehive.common.ui.styles import apply_primary_btn
from beehive.features.wokbee.settings import WokBeeSettings
from beehive.features.wokbee.ui.project_settings_form import ProjectSettingsForm


class WokBeeSettingsWorkspace(QWidget):
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
        self.form.load()

    def _build(self):
        c = self.theme.colors
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setStyleSheet(f"background: {c['content_bg']}; border: none;")
        hl = QVBoxLayout(header)
        hl.setContentsMargins(28, 20, 28, 12)
        title = QLabel("WokBee 设置")
        title.setStyleSheet(
            f"font-size: 20px; font-weight: bold; color: {c['text']};"
            "background: transparent; border: none;"
        )
        hl.addWidget(title)
        tip = QLabel(
            "配置工作区根目录、默认审核勾选与模型。"
            "新建项目会拷贝此处的审核策略，之后可在项目内单独修改。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(
            f"font-size: 12px; color: {c['text_hint']};"
            "background: transparent; border: none;"
        )
        hl.addWidget(tip)
        root.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(28, 16, 28, 16)
        bl.setSpacing(18)

        self.form = ProjectSettingsForm(self.theme, self.settings, body)
        bl.addWidget(self.form)
        bl.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, stretch=1)

        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(28, 10, 28, 18)
        btn_bar.addStretch()
        save_btn = QPushButton("保存 WokBee 设置")
        apply_primary_btn(save_btn, c)
        save_btn.clicked.connect(self._on_save)
        btn_bar.addWidget(save_btn)
        root.addLayout(btn_bar)

    def showEvent(self, event):
        super().showEvent(event)
        self.form._reload_models()

    def _on_save(self):
        root = self.form._ws_edit.text().strip()
        if not root:
            _tip(self, self.theme, "请先设置工作区根目录。")
            return
        try:
            Path(root).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            _tip(self, self.theme, f"无法创建工作区目录：{e}")
            return
        self.form.apply()
        self.settings.save()
        _tip(self, self.theme, "WokBee 设置已保存。")
