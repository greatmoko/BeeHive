"""记忆页面组装；全局文档、提案、原子列表和阈值由独立控件管理。"""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from beehive.common.ui.theme import COLORS
from beehive.system.memory.store import MemoryStore
from beehive.system.memory.ui.memory_thresholds import MemoryThresholds
from beehive.system.memory.ui.global_memory_editor import GlobalMemoryEditor
from beehive.system.memory.ui.memory_proposals import MemoryProposals
from beehive.system.memory.ui.atomic_memory_panel import AtomicMemoryPanel


class MemoryWorkspace(QWidget):
    def __init__(self, theme, parent=None, *, store=None, _mode="all"):
        super().__init__(parent)
        self.store = store or MemoryStore()
        self._mode = _mode
        colors = getattr(theme, "colors", COLORS)
        root = QVBoxLayout(self)
        self.thresholds = self.global_editor = self.proposals = self.atomic = None
        if _mode != "atomic":
            root.addWidget(QLabel("记忆系统 · WokBee / DeziBee"))
            self.thresholds = MemoryThresholds(colors, self.store, self)
            self.global_editor = GlobalMemoryEditor(colors, self.store, self)
            self.proposals = MemoryProposals(colors, self.store, self)
            self.global_editor.updated.connect(self.proposals.refresh)
            self.proposals.changed.connect(self.refresh)
            root.addWidget(self.thresholds)
            root.addWidget(self.global_editor, 1)
            root.addWidget(self.proposals)
        if _mode != "global":
            self.atomic = AtomicMemoryPanel(colors, self.store, self)
            root.addWidget(self.atomic, 1)
        self.refresh()

    def refresh(self):
        if self.thresholds:
            self.thresholds.refresh()
        if self.global_editor:
            self.global_editor.refresh()
        if self.atomic:
            self.atomic.refresh_atomic_memories(reset_page=False)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()


class GlobalMemoryWorkspace(MemoryWorkspace):
    def __init__(self, theme, parent=None, *, store=None):
        super().__init__(theme, parent, store=store, _mode="global")


class AtomicMemoryWorkspace(MemoryWorkspace):
    def __init__(self, theme, parent=None, *, store=None):
        super().__init__(theme, parent, store=store, _mode="atomic")
