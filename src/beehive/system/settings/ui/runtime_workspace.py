"""全局系统运行设置页，不引用业务存储或业务页面。"""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QPushButton, QLabel
from beehive.common.ui.styles.labels import title_label_qss
from beehive.common.ui.styles.buttons import apply_primary_btn
from beehive.common.ui.dialogs.message import show_tip
from beehive.system.settings.runtime_settings import RuntimeSettings
from beehive.system.settings.ui.runtime_form import RuntimeSettingsForm
from beehive.system.settings.ui.directory_access import DirectoryAccessForm


class RuntimeSettingsWorkspace(QWidget):
    def __init__(self, theme, settings=None, parent=None):
        super().__init__(parent)
        self.theme, self.settings = theme, settings or RuntimeSettings()
        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 20, 28, 18)
        layout.setSpacing(18)
        title = QLabel("系统运行设置")
        title.setStyleSheet(title_label_qss(theme.colors))
        layout.addWidget(title)
        self.runtime = RuntimeSettingsForm(theme, self.settings)
        self.directories = DirectoryAccessForm(theme, self.settings.directories)
        layout.addWidget(self.runtime)
        layout.addWidget(self.directories)
        layout.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll)
        save = QPushButton("保存系统设置")
        apply_primary_btn(save, theme.colors, height=34)
        save.clicked.connect(self.save)
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(28, 10, 28, 18)
        btn_bar.addStretch()
        btn_bar.addWidget(save)
        root.addLayout(btn_bar)
        self.load()

    def load(self):
        self.runtime.load()
        self.directories._reload_ext_dirs()

    def save(self):
        self.runtime.apply()
        self.settings.save()
        show_tip(self, self.theme, "系统设置已保存。")

    def showEvent(self, event):
        super().showEvent(event)
        self.load()
