"""设置页装配：系统环境与各业务数据清理入口。"""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QFrame, QLabel, QScrollArea, QStackedWidget
from beehive.common.ui.theme import Theme
from beehive.common.ui.styles.labels import title_label_qss
from beehive.features.tokbee.core.chat_manager import ChatManager
from beehive.features.wokbee.projects.project_store import ProjectStore
from beehive.features.tokbee.ui.views.chat_cleanup import ChatCleanupPanel
from beehive.features.wokbee.ui.project_cleanup import ProjectCleanupPanel
from beehive.system.settings.ui.runtime_environment import RuntimeEnvironmentPanel
from beehive.app.shell.settings_navigation import _SubNav


class _GeneralWorkspace(QWidget):
    chats_cleared = Signal()
    projects_cleared = Signal()

    def __init__(self, theme, chat_manager, project_store=None, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(20)
        title = QLabel("通用设置")
        title.setStyleSheet(title_label_qss(theme.colors))
        layout.addWidget(title)
        chats = ChatCleanupPanel(theme, chat_manager, body)
        projects = ProjectCleanupPanel(theme, project_store, body)
        environment = RuntimeEnvironmentPanel(theme, parent=body)
        chats.chats_cleared.connect(self.chats_cleared)
        projects.projects_cleared.connect(self.projects_cleared)
        for panel in [chats, projects, environment]:
            separator = QFrame()
            separator.setFrameShape(QFrame.Shape.HLine)
            separator.setStyleSheet(f"color: {theme.colors['border_light']};")
            layout.addWidget(separator)
            layout.addWidget(panel)
        layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll)



class SettingsView(QWidget):
    """设置页：左侧二级导航 + 右侧工作区。"""

    chats_cleared = Signal()
    projects_cleared = Signal()

    def __init__(
        self,
        theme: Theme,
        chat_manager: ChatManager,
        project_store: ProjectStore | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.theme = theme
        self._chat_manager = chat_manager
        self._project_store = project_store
        self._pages: dict[str, QWidget] = {}
        self._build()

    def _build(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._subnav = _SubNav(self.theme)
        self._subnav.nav_changed.connect(self._switch_page)
        layout.addWidget(self._subnav)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background: {self.theme.colors['content_bg']};")
        layout.addWidget(self._stack, stretch=1)

        general_page = _GeneralWorkspace(
            self.theme, self._chat_manager, project_store=self._project_store
        )
        general_page.chats_cleared.connect(self.chats_cleared)
        general_page.projects_cleared.connect(self.projects_cleared)
        self._pages["general"] = general_page
        self._stack.addWidget(general_page)

        self._subnav.select("general")

    def _switch_page(self, nav_id: str):
        page = self._pages.get(nav_id)
        if page:
            self._stack.setCurrentWidget(page)
