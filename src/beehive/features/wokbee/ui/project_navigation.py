"""项目列表、选择及菜单操作。"""
from __future__ import annotations
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget
from beehive.common.ui.styles.menus import make_context_menu
from beehive.common.ui.styles import apply_lineedit, apply_icon_btn
from beehive.common.ui.theme import Theme
from beehive.features.wokbee.projects.models import MAX_PROJECT_TITLE_LEN, ProjectStatus
from beehive.features.wokbee.projects.project_store import ProjectStore, TRASH_RETENTION_DAYS
from beehive.common.ui.dialogs.text_input import _ask_text
from beehive.common.ui.dialogs.message import _confirm
from beehive.features.wokbee.ui.dialogs import _default_project_title
from beehive.common.process.desktop import open_path as _open_in_explorer
from beehive.features.wokbee.ui.project_item import _ProjectItem
class _ProjectSidebar(QFrame):
    project_selected = Signal(str)
    project_changed = Signal()

    def __init__(self, theme: Theme, store: ProjectStore, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.store = store
        self._selected_id: str | None = None
        self._build()
        self.refresh()

    def _build(self):
        c = self.theme.colors
        self.setMinimumWidth(200)
        self.setMaximumWidth(240)
        self.setStyleSheet(f"""
            _ProjectSidebar {{
                background: {c["subnav_bg"]};
                border-right: 1px solid {c["border"]};
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索项目...")
        apply_lineedit(self._search, c)
        self._search.textChanged.connect(lambda: self.refresh())
        search_row.addWidget(self._search, stretch=1)

        new_btn = QPushButton("＋")
        new_btn.setToolTip("新建项目")
        apply_icon_btn(new_btn, c, size=34)
        new_btn.clicked.connect(self._on_new)
        search_row.addWidget(new_btn)
        layout.addLayout(search_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._list_container = QWidget()
        self._list_container.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(2)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._list_container)
        layout.addWidget(scroll, stretch=1)

    def refresh(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        projects = self.store.search(self._search.text())
        c = self.theme.colors
        if not projects:
            empty = QLabel("暂无项目")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(f"font-size: 12px; color: {c['text_hint']}; padding: 20px 0;")
            self._list_layout.addWidget(empty)
            return

        for p in projects:
            item = _ProjectItem(p, self.theme, selected=(p.id == self._selected_id))
            item.clicked.connect(self._on_select)
            item.context_menu.connect(self._on_context)
            self._list_layout.addWidget(item)

    def select(self, project_id: str):
        self._selected_id = project_id
        self.refresh()
        self.project_selected.emit(project_id)

    def _on_select(self, project_id: str):
        self.select(project_id)

    def _on_new(self):
        # 直接创建空项目，名称默认「项目+时间」，目标稍后在详情里补
        project = self.store.create(title=_default_project_title(), goal="")
        self.project_changed.emit()
        self.select(project.id)

    def _on_context(self, project_id: str, pos):
        c = self.theme.colors
        menu = make_context_menu(self, c)
        rename_a = menu.addAction("重命名")
        open_a = menu.addAction("打开工作文件夹")
        copy_a = menu.addAction("复制项目 ID")
        project = self.store.get(project_id)
        pin_a = menu.addAction(
            "取消置顶" if project and project.pinned else "置顶"
        )
        menu.addSeparator()
        del_a = menu.addAction("删除（移入回收）")
        if project and project.pinned:
            del_a.setEnabled(False)
            del_a.setText("删除（请先取消置顶）")
        elif project and project.status in (
            ProjectStatus.RUNNING, ProjectStatus.AWAITING_APPROVAL,
        ):
            del_a.setEnabled(False)
            del_a.setText("删除（运行中，请先暂停）")
        action = menu.exec(pos)
        if action == rename_a:
            project = self.store.get(project_id)
            if not project:
                return
            new_title = _ask_text(
                self,
                self.theme,
                "重命名",
                f"新名称（最多 {MAX_PROJECT_TITLE_LEN} 字）",
                project.title[:MAX_PROJECT_TITLE_LEN],
                max_length=MAX_PROJECT_TITLE_LEN,
            )
            if new_title:
                self.store.rename(project_id, new_title)
                self.refresh()
                self.project_changed.emit()
                if self._selected_id == project_id:
                    self.project_selected.emit(project_id)
        elif action == open_a:
            path = self.store.path_for(project_id)
            _open_in_explorer(path)
        elif action == copy_a:
            QApplication.clipboard().setText(project_id)
        elif action == pin_a:
            self.store.toggle_pin(project_id)
            self.refresh()
            self.project_changed.emit()
        elif action == del_a:
            project = self.store.get(project_id)
            if project and project.pinned:
                return
            if project and project.status in (
                ProjectStatus.RUNNING, ProjectStatus.AWAITING_APPROVAL,
            ):
                return
            if _confirm(
                self,
                self.theme,
                "删除项目",
                f"确定将该项目移入工作区 _trash？\n"
                f"回收站最多保留 {TRASH_RETENTION_DAYS} 天，过期将永久删除。",
            ):
                if not self.store.delete(project_id, trash=True):
                    return
                if self._selected_id == project_id:
                    self._selected_id = None
                self.refresh()
                self.project_changed.emit()
                projects = self.store.list_projects()
                if projects:
                    self.select(projects[0].id)
                else:
                    self.project_selected.emit("")
