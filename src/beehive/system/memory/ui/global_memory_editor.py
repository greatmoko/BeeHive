"""全局记忆文档编辑；保存、重置均保留明确用户动作和错误处理。"""
import sqlite3
from types import SimpleNamespace
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget
from beehive.common.ui.styles.buttons import apply_danger_btn, apply_primary_btn, apply_secondary_btn
from beehive.common.ui.styles.inputs import apply_textedit
from beehive.common.ui.dialogs.message import show_tip as _tip, _confirm
from PySide6.QtCore import Signal
from beehive.system.memory.ui.memory_document import _parse_global_memory_text


class GlobalMemoryEditor(QWidget):
    updated = Signal()

    def __init__(self, colors, store, parent=None):
        super().__init__(parent)
        self._colors, self.store = colors, store
        # 公共弹窗按 Theme 签名调用（只需 .colors），此处用 colors dict 适配。
        self._theme = SimpleNamespace(colors=colors)
        self._sync_initial_environment()
        global_card = self
        global_layout = QVBoxLayout(global_card)
        global_layout.addWidget(QLabel("全局记忆（单份）"))
        global_layout.addWidget(QLabel("可直接编辑并保存，AI 建议仍需手动确认。"))
        self.content = QTextEdit()
        apply_textedit(self.content, self._colors)
        global_layout.addWidget(self.content)
        save_memory = QPushButton("保存全局记忆")
        apply_primary_btn(save_memory, self._colors)
        save_memory.setFixedWidth(140)
        save_memory.clicked.connect(self.save_global_memory)

        actions_row = QWidget()
        actions_layout = QHBoxLayout(actions_row)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.addStretch()
        actions_layout.addWidget(save_memory)
        refresh = QPushButton("刷新记忆")
        apply_secondary_btn(refresh, self._colors)
        refresh.setFixedWidth(100)
        refresh.clicked.connect(self.refresh)
        actions_layout.addWidget(refresh)
        reset_memory = QPushButton("一键重置")
        apply_danger_btn(reset_memory, self._colors)
        reset_memory.setFixedWidth(100)
        reset_memory.clicked.connect(self.reset_global_memory)
        actions_layout.addWidget(reset_memory)
        global_layout.addWidget(actions_row)


    def _settings_environment(self):
        try:
            from beehive.system.runtime.runtime_env_format import build_runtime_env_settings_text

            text = build_runtime_env_settings_text().strip()
        except Exception:
            return ""
        if not text or text.startswith("尚未探测本机环境"):
            return ""
        return text

    def _sync_initial_environment(self):
        environment = self._settings_environment()
        if environment:
            try:
                self.store.ensure_environment(environment)
            except (ValueError, OSError, sqlite3.Error):
                pass

    def refresh(self):
        snapshot = self.store.global_memory()
        self.content.setPlainText(self.store.global_text(snapshot))
        self.updated.emit()


    def save_global_memory(self):
        try:
            changed = self.store.save_global(
                _parse_global_memory_text(self.content.toPlainText())
            )
        except (ValueError, OSError, sqlite3.Error) as exc:
            _tip(self, self._theme, str(exc), title="无法保存全局记忆")
            return
        self.refresh()
        _tip(self, self._theme, "全局记忆已保存。" if changed else "内容未变化。", title="已保存")

    def reset_global_memory(self):
        if not _confirm(
            self,
            self._theme,
            "一键重置全局记忆",
            "确定恢复初始模板吗？用户画像和自定义全局规则会被清空，待处理更新会被废弃。",
        ):
            return
        try:
            environment = self._settings_environment()
            if not environment:
                environment = self.store.global_memory()["content"].get("环境信息", "")
            changed = self.store.reset_global_memory(environment)
        except (ValueError, OSError, sqlite3.Error) as exc:
            _tip(self, self._theme, str(exc), title="无法重置全局记忆")
            return
        self.refresh()
        _tip(
            self,
            self._theme,
            "全局记忆已恢复初始模板。" if changed else "全局记忆已经是初始模板。",
            title="已重置",
        )
