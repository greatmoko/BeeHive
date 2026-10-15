"""主窗口布局 — 三栏结构：一级导航 | 二级导航 | 工作区。"""

import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QMainWindow, QWidget, QHBoxLayout, QStackedWidget

from beehive import __app_name__, __version__
from beehive.system.settings.config import Config
from beehive.common.ui.theme import Theme

_RESOURCES = Path(__file__).resolve().parents[2] / "common/ui/resources"


# ────────────────────────── 一级导航栏 ──────────────────────────



from beehive.app.shell.navigation import _PrimaryNav


# ────────────────────────── 主窗口 ──────────────────────────

class MainWindow(QMainWindow):
    """主窗口：一级导航 | 二级导航+工作区。"""

    def __init__(self, config: Config, theme: Theme):
        super().__init__()
        self.theme = theme
        self._views: dict[str, QWidget] = {}

        from beehive.app.services import ServiceRegistry
        self._services = ServiceRegistry(config)

        self._setup_window()
        self._build_layout()
        self._register_views()
        self._set_titlebar_color()

        self._nav.select("chat")

    def _setup_window(self):
        self.setWindowTitle(f"{__app_name__} v{__version__}")
        self.resize(960, 640)
        self.setMinimumSize(QSize(720, 480))

        icon = QIcon()
        ico_path = _RESOURCES / "icon.ico"
        logo_path = _RESOURCES / "logo.png"
        if ico_path.exists():
            icon.addFile(str(ico_path))
        if logo_path.exists():
            pm = QPixmap(str(logo_path))
            if not pm.isNull():
                for size in (16, 24, 32, 48, 64, 128, 256):
                    icon.addPixmap(
                        pm.scaled(
                            size,
                            size,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
        if not icon.isNull():
            self.setWindowIcon(icon)

        self.setStyleSheet(self.theme.stylesheet())

    def _build_layout(self):
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._nav = _PrimaryNav(self.theme)
        self._nav.nav_changed.connect(self._switch_view)
        root_layout.addWidget(self._nav)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent;")
        root_layout.addWidget(self._stack, stretch=1)

    def _register_views(self):
        from beehive.features.tokbee.ui.views.chat_workspace import ChatView
        from beehive.app.shell.settings_view import SettingsView
        from beehive.app.shell.configuration_view import ConfigurationView
        from beehive.system.providers.provider_store import ProviderStore
        from beehive.features.wokbee.ui.wokbee_view import WokBeeView
        from beehive.features.autobee.ui.autobee_view import AutoBeeView
        from beehive.features.dezibee.ui.dezibee_view import DeziBeeView

        svc = self._services

        chat = ChatView(self.theme, svc.role_manager)
        wokbee = WokBeeView(
            self.theme,
            store=svc.wokbee_store,
            settings=svc.wokbee_settings,
            gateway_manager=svc.gateway_manager,
        )
        settings = SettingsView(
            self.theme, chat.manager, project_store=svc.wokbee_store
        )
        settings.chats_cleared.connect(chat.refresh_after_clear)
        settings.projects_cleared.connect(wokbee.refresh_after_projects_cleared)

        automation = ConfigurationView(
            self.theme,
            svc.role_manager,
            wokbee_settings=svc.wokbee_settings,
            gateway_manager=svc.gateway_manager,
        )
        autobee = AutoBeeView(
            self.theme,
            store=svc.autobee_store,
            scheduler=svc.autobee_scheduler,
            provider_store=ProviderStore(),
            project_store=svc.wokbee_store,
            gateway_manager=svc.gateway_manager,
        )
        dezibee = DeziBeeView(self.theme)

        self._views["chat"] = chat
        self._views["wokbee"] = wokbee
        self._views["autobee"] = autobee
        self._views["dezibee"] = dezibee
        self._views["automation"] = automation
        self._views["settings"] = settings

        self._stack.addWidget(chat)
        self._stack.addWidget(wokbee)
        self._stack.addWidget(autobee)
        self._stack.addWidget(dezibee)
        self._stack.addWidget(automation)
        self._stack.addWidget(settings)

        # 启动定时任务调度（幂等）
        svc.autobee_scheduler.start()
        # 启动消息网关（幂等；未启用会打印提示而不报错）
        svc.gateway_manager.start()

    def _switch_view(self, nav_id: str):
        view = self._views.get(nav_id)
        if view:
            self._stack.setCurrentWidget(view)

    def closeEvent(self, event):
        dezibee = self._views.get("dezibee")
        if dezibee is not None and dezibee.shutdown() is False:
            # 取消收尾期间保留页面及线程所有者。
            event.ignore()
            from PySide6.QtCore import QTimer
            QTimer.singleShot(500, self.close)
            return
        autobee = self._views.get("autobee")
        if autobee is not None and autobee.shutdown() is False:
            event.ignore()
            from PySide6.QtCore import QTimer
            QTimer.singleShot(500, self.close)
            return
        try:
            self._services.autobee_scheduler.shutdown(wait=False)
        except Exception:
            pass
        try:
            self._services.gateway_manager.shutdown(wait=False)
        except Exception:
            pass
        # 先收尾后台线程（agent/lesson/压缩/改名），避免 QThread 仍在运行时销毁
        wokbee = self._views.get("wokbee")
        if wokbee is not None:
            try:
                wokbee.shutdown()
            except Exception:
                pass
        super().closeEvent(event)

    def _set_titlebar_color(self):
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            color = 0x00FFFFFF
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 35,
                ctypes.byref(ctypes.c_int(color)),
                ctypes.sizeof(ctypes.c_int),
            )
        except Exception:
            pass
