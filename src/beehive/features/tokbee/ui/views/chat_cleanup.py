"""非置顶聊天清理，保留用户确认与置顶保护的业务调用。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from beehive.common.ui.styles.buttons import destructive_btn_qss, apply_danger_btn
from beehive.common.ui.styles.labels import style_hint_label
from beehive.common.ui.dialogs.compact import show_cleanup_result, show_compact_tip
from beehive.common.ui.dialogs.message import _confirm


class ChatCleanupPanel(QWidget):
    chats_cleared = Signal()

    def __init__(self, theme, manager, parent=None):
        super().__init__(parent)
        self.theme, self.manager = theme, manager
        c = theme.colors
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        group_label = QLabel("聊天记录管理")
        group_label.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {c['text']}; margin-top: 4px;")
        layout.addWidget(group_label)

        row = QHBoxLayout()
        row.setSpacing(12)
        desc = QLabel("删除所有未置顶的聊天记录，置顶对话不受影响。")
        desc.setStyleSheet(f"font-size: 13px; color: {c['text_secondary']};")
        desc.setWordWrap(True)
        row.addWidget(desc, stretch=1)

        clear_btn = QPushButton("清空非置顶聊天")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setFixedHeight(34)
        clear_btn.setStyleSheet(destructive_btn_qss(self.theme.colors))
        clear_btn.clicked.connect(self._confirm_clear)
        row.addWidget(clear_btn)

        layout.addLayout(row)



    def _confirm_clear(self):
        if not _confirm(self, self.theme, "确认清空",
                        "确定要删除所有非置顶的聊天记录吗？\n\n此操作不可撤销，置顶对话不受影响。"):
            return
        count = self.manager.delete_unpinned()
        self.chats_cleared.emit()
        show_cleanup_result(self, self.theme, f"已清空 {count} 条非置顶聊天记录。" if count > 0 else "没有需要清空的聊天记录。")
