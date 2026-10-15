"""项目清理保留业务归属，确认后通过原存储移入回收站。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QPushButton, QDialog
from beehive.common.ui.styles.buttons import destructive_btn_qss, apply_danger_btn
from beehive.common.ui.styles.labels import style_hint_label
from beehive.common.ui.dialogs.compact import show_cleanup_result, show_compact_tip
from beehive.features.wokbee.projects.project_store import TRASH_RETENTION_DAYS


class ProjectCleanupPanel(QWidget):
    projects_cleared = Signal()

    def __init__(self, theme, store, parent=None):
        super().__init__(parent)
        self.theme, self.project_store = theme, store
        c = theme.colors
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        wb_group = QLabel("WokBee 项目记录管理")
        wb_group.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {c['text']}; margin-top: 4px;")
        layout.addWidget(wb_group)

        wb_row = QHBoxLayout()
        wb_row.setSpacing(12)
        wb_desc = QLabel(
            f"删除所有未置顶项目（移入工作区 _trash，与 WokBee 页面删除一致），"
            f"置顶项目不受影响。回收站最多保留 {TRASH_RETENTION_DAYS} 天。"
        )
        wb_desc.setWordWrap(True)
        style_hint_label(wb_desc, c)
        wb_row.addWidget(wb_desc, stretch=1)

        wb_btn = QPushButton("删除非置顶项目")
        wb_btn.setFixedHeight(34)
        wb_btn.setStyleSheet(destructive_btn_qss(self.theme.colors))
        wb_btn.setToolTip("与 WokBee 侧栏删除相同：未置顶移入回收站，置顶跳过")
        wb_btn.clicked.connect(self._confirm_clear_projects)
        wb_row.addWidget(wb_btn)
        layout.addLayout(wb_row)



    def _confirm_clear_projects(self):
        if self.project_store is None:
            show_compact_tip(self, self.theme, "项目存储未就绪")
            return
        c = self.theme.colors
        dlg = QDialog(self)
        dlg.setWindowTitle("确认删除")
        dlg.setFixedSize(420, 200)
        dlg.setStyleSheet(f"background: {c['content_bg']};")

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(12)

        msg = QLabel(
            "确定删除所有未置顶的 WokBee 项目吗？\n\n"
            f"与 WokBee 页面删除相同：移入工作区 _trash，回收站最多保留 "
            f"{TRASH_RETENTION_DAYS} 天。置顶与运行中的项目不会删除。"
        )
        msg.setWordWrap(True)
        msg.setStyleSheet(f"font-size: 13px; color: {c['text']};")
        layout.addWidget(msg)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedSize(80, 34)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c["card_bg"]};
                color: {c["text"]};
                border: 1px solid {c["border"]};
                border-radius: 6px;
                font-size: 13px;
            }}
        """)
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)

        confirm_btn = QPushButton("确认删除")
        confirm_btn.setFixedSize(100, 34)
        confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_danger_btn(confirm_btn, c, height=34)
        confirm_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(confirm_btn)

        layout.addLayout(btn_row)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            count = self.project_store.delete_unpinned(trash=True)
            self.projects_cleared.emit()
            show_cleanup_result(self, self.theme, f"已将 {count} 个未置顶项目移入回收站。" if count > 0 else "没有需要删除的未置顶项目。")
