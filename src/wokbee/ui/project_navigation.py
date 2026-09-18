"""WokBee 工作区：侧栏项目列表、项目要素、后台 worker、与三段式整体工作区。

聚合了四种自有定义：侧栏/项目列表、项目要素、后台 worker（上下文压缩 / AI 改名）、
以及把时间线 + 操作栏 + 侧栏拼成一体的 `_ProjectWorkspace`。时间线与操作栏本身
分别位于 `timeline.py` / `action_bar.py`。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tokbee.core import context_manager as ctxman
from tokbee.core.ai_client import AIClient
from tokbee.core.provider_store import ProviderStore, ResolvedModel
from tokbee.core.session_settings import SessionSettings, ProviderOptions
from tokbee.ui.styles.system import make_context_menu
from tokbee.ui.styles.theme import Theme

from wokbee.core.context_usage import (
    estimate_project_usage,
    load_context_state,
    plan_project_compaction,
    save_context_state,
)
from wokbee.core.models import MAX_PROJECT_TITLE_LEN, Project, ProjectEvent, ProjectStatus
from wokbee.core.paths import deliverables_dir, list_deliverable_names, uploads_dir
from wokbee.core.project_store import MAX_ARCHIVES, ProjectStore, TRASH_RETENTION_DAYS
from wokbee.ui.action_bar import _ActionBar
from wokbee.ui.ask_user_dialog import AskUserDialog
from wokbee.ui.dialogs import (
    _ask_multiline,
    _ask_text,
    _confirm,
    _default_project_title,
    _prompt_approval_flags,
    open_path as _open_in_explorer,
    tip as _tip,
)
from wokbee.ui.timeline import INITIAL_RENDER
from wokbee.ui.web_chat import _WebChat


logger = logging.getLogger("wokbee")


def _model_request_settings(model: ResolvedModel) -> SessionSettings:
    """将厂商-模型配置转换为 TokBee 客户端请求参数。"""
    return SessionSettings(
        temperature=model.temperature,
        top_p=model.top_p,
        max_tokens=model.max_tokens,
        stream=model.stream,
        provider_options=ProviderOptions(
            reasoning_adapter=model.reasoning_adapter,
            reasoning_effort=model.reasoning_effort,
            reasoning_enabled=model.reasoning_enabled,
        ),
    )


STATUS_LABEL = {
    ProjectStatus.IDLE: "空闲",
    ProjectStatus.RUNNING: "运行中",
    ProjectStatus.AWAITING_APPROVAL: "待审批",
    ProjectStatus.FAILED: "失败",
    ProjectStatus.DONE: "完成",
}

STATUS_COLOR_KEY = {
    ProjectStatus.IDLE: "text_hint",
    ProjectStatus.RUNNING: "accent",
    ProjectStatus.AWAITING_APPROVAL: "warning",
    ProjectStatus.FAILED: "danger",
    ProjectStatus.DONE: "success",
}

# 新建项目时，名称默认取目标前 N 个字（也是名称硬上限）
TITLE_FROM_GOAL_LEN = MAX_PROJECT_TITLE_LEN


class _ProjectItem(QFrame):
    clicked = Signal(str)
    context_menu = Signal(str, object)

    def __init__(self, project: Project, theme: Theme, selected: bool = False, parent=None):
        super().__init__(parent)
        self.project = project
        self.theme = theme
        self._selected = selected
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(62)
        self._build()

    def _build(self):
        c = self.theme.colors
        p = self.project
        bg = c["accent_light"] if self._selected else "transparent"
        border = (
            f"border-left: 3px solid {c['accent']};"
            if self._selected
            else "border-left: 3px solid transparent;"
        )
        self.setStyleSheet(f"""
            _ProjectItem {{
                background: {bg};
                border-radius: 6px;
                {border}
            }}
            _ProjectItem:hover {{ background: {c["subnav_hover"]}; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        top = QHBoxLayout()
        color = c[STATUS_COLOR_KEY.get(p.status, "text_hint")]
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {color}; font-size: 10px; background: transparent; border: none;")
        top.addWidget(dot)

        title = QLabel(p.title)
        title.setWordWrap(False)
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        title.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {c['text']};"
            "background: transparent; border: none;"
        )
        top.addWidget(title, 1)
        if p.pinned:
            pin = QLabel("📌")
            pin.setStyleSheet(
                "font-size: 11px; background: transparent; border: none;"
            )
            pin.setFixedWidth(16)
            top.addWidget(pin, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(top)

        try:
            dt = datetime.strptime(p.updated_at, "%Y-%m-%d %H:%M:%S")
            time_str = dt.strftime("%m-%d %H:%M")
        except ValueError:
            time_str = p.updated_at

        status = STATUS_LABEL.get(p.status, p.status.value)
        info = QLabel(f"{status} · {time_str}")
        info.setStyleSheet(
            f"font-size: 11px; color: {c['text_hint']}; background: transparent; border: none;"
        )
        layout.addWidget(info)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.project.id)
        elif event.button() == Qt.MouseButton.RightButton:
            self.context_menu.emit(self.project.id, event.globalPosition().toPoint())
        super().mousePressEvent(event)


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
        self._search.setFixedHeight(30)
        self._search.textChanged.connect(lambda: self.refresh())
        self._search.setStyleSheet(f"""
            QLineEdit {{
                background: {c["input_bg"]}; color: {c["text"]};
                border: 1px solid {c["input_border"]}; border-radius: 6px; padding: 0 8px;
            }}
        """)
        search_row.addWidget(self._search, stretch=1)

        new_btn = QPushButton("＋")
        new_btn.setToolTip("新建项目")
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setFixedSize(30, 30)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_bg"]}; color: {c["text"]};
                border: none; border-radius: 6px; font-size: 16px;
            }}
            QPushButton:hover {{ background: {c["btn_hover"]}; }}
        """)
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
