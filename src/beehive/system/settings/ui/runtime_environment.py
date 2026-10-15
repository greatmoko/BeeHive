"""本机运行环境缓存显示与用户触发的重新探测。"""
import threading
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit
from beehive.common.ui.styles.inputs import apply_textedit
from beehive.common.ui.styles.buttons import apply_secondary_btn
from beehive.common.ui.styles.labels import style_hint_label
from beehive.system.settings.runtime_settings import RuntimeSettings
from beehive.system.runtime.runtime_env_format import build_runtime_env_settings_text
from beehive.system.runtime.runtime_env_cache import ensure_runtime_env


class RuntimeEnvironmentPanel(QWidget):
    _env_text_ready = Signal(str)

    def __init__(self, theme, settings=None, parent=None):
        super().__init__(parent)
        self.theme, self.settings = theme, settings or RuntimeSettings()
        self._env_text_ready.connect(self._apply_env_text)
        c = theme.colors
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        env_group = QLabel("本机运行环境")
        env_group.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {c['text']}; margin-top: 4px;")
        layout.addWidget(env_group)

        env_hint = QLabel(
            "首次为空时自动探测并保存到本机配置；之后所有 Agent 直接加载此缓存，不会每次重扫。"
            "安装新软件后可点「重新探测」刷新。"
        )
        env_hint.setWordWrap(True)
        style_hint_label(env_hint, c)
        layout.addWidget(env_hint)

        self._env_edit = QTextEdit()
        self._env_edit.setReadOnly(True)
        self._env_edit.setMinimumHeight(220)
        apply_textedit(self._env_edit, c)
        layout.addWidget(self._env_edit)

        env_btn_row = QHBoxLayout()
        env_btn_row.addStretch()
        self._env_refresh_btn = QPushButton("重新探测")
        self._env_refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_secondary_btn(self._env_refresh_btn, c, height=34)
        self._env_refresh_btn.clicked.connect(self._refresh_env)
        env_btn_row.addWidget(self._env_refresh_btn)
        layout.addLayout(env_btn_row)


        self._reload_env_text()

    def showEvent(self, event):
        super().showEvent(event)
        self._reload_env_text()

    def _reload_env_text(self):
        self._apply_env_text(build_runtime_env_settings_text(self.settings))

    def _apply_env_text(self, text: str):
        self._env_edit.setPlainText(text)
        self._env_refresh_btn.setEnabled(True)

    def _refresh_env(self):
        self._env_refresh_btn.setEnabled(False)
        self._env_edit.setPlainText("正在重新探测本机环境，请稍候…")

        settings = self.settings

        def _worker():
            try:
                ensure_runtime_env(settings, force=True)
                text = build_runtime_env_settings_text(settings)
            except Exception as e:
                text = f"探测失败：{e}"
            self._env_text_ready.emit(text)

        threading.Thread(target=_worker, daemon=True, name="settings-runtime-env").start()
