"""AI 配置页面 — AI 角色 / 厂商设置 / TokBee 设置。"""
from __future__ import annotations



import logging

from PySide6.QtWidgets import QWidget, QHBoxLayout, QStackedWidget

from beehive.common.ui.theme import Theme
from beehive.features.tokbee.core.ai_role import AIRoleManager

logger = logging.getLogger("tokbee")




from beehive.app.shell.configuration_navigation import _SubNav


# ═══════════════════════════════════════════════════════════════
# AI 角色管理
# ═══════════════════════════════════════════════════════════════







from beehive.features.tokbee.ui.roles.workspace import _AIRoleWorkspace


class ConfigurationView(QWidget):
    """AI 配置页：左侧二级导航 + 右侧工作区。"""

    def __init__(
        self,
        theme: Theme,
        role_manager: AIRoleManager | None = None,
        wokbee_settings=None,
        gateway_manager=None,
        parent=None,
    ):
        super().__init__(parent)
        self.theme = theme
        self._role_manager = role_manager or AIRoleManager()
        self._wokbee_settings = wokbee_settings
        self._gateway_manager = gateway_manager
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

        role_page = _AIRoleWorkspace(self.theme, self._role_manager)
        self._pages["ai_roles"] = role_page
        self._stack.addWidget(role_page)

        from beehive.system.providers.ui.provider_view import ProviderSettingsWorkspace
        model_page = ProviderSettingsWorkspace(self.theme)
        self._pages["model_config"] = model_page
        self._stack.addWidget(model_page)

        from beehive.features.tokbee.ui.views.session_defaults_view import SessionDefaultsWorkspace
        defaults_page = SessionDefaultsWorkspace(self.theme, self._role_manager)
        self._pages["session_defaults"] = defaults_page
        self._stack.addWidget(defaults_page)

        from beehive.features.wokbee.settings import WokBeeSettings
        ab_settings = self._wokbee_settings or WokBeeSettings()
        from beehive.system.settings.ui.runtime_workspace import RuntimeSettingsWorkspace
        runtime_page = RuntimeSettingsWorkspace(self.theme, ab_settings.system)
        self._pages["system_settings"] = runtime_page
        self._stack.addWidget(runtime_page)

        from beehive.features.wokbee.ui.settings_workspace import WokBeeSettingsWorkspace
        ab_page = WokBeeSettingsWorkspace(self.theme, ab_settings)
        self._pages["wokbee_settings"] = ab_page
        self._stack.addWidget(ab_page)

        from beehive.features.dezibee.ui.settings_workspace import DeziBeeSettingsWorkspace
        dz_page = DeziBeeSettingsWorkspace(self.theme, ab_settings)
        self._pages["dezibee_settings"] = dz_page
        self._stack.addWidget(dz_page)

        from beehive.system.memory.ui.memory_workspace import GlobalMemoryWorkspace, AtomicMemoryWorkspace
        for key, page_cls in (("global_memory", GlobalMemoryWorkspace), ("atomic_memory", AtomicMemoryWorkspace)):
            page = page_cls(self.theme)
            self._pages[key] = page
            self._stack.addWidget(page)

        from beehive.system.skills.ui.skills_workspace import SkillsWorkspace
        from beehive.system.skills.skills_store import SkillsStore
        skills_page = SkillsWorkspace(self.theme, SkillsStore())
        self._pages["skills"] = skills_page
        self._stack.addWidget(skills_page)

        from beehive.system.credentials.ui.credential_view import CredentialWorkspace
        cred_page = CredentialWorkspace(self.theme)
        self._pages["credentials"] = cred_page
        self._stack.addWidget(cred_page)

        from beehive.system.mcp.ui.mcp_workspace import McpWorkspace
        from beehive.system.mcp.mcp_store import McpStore
        mcp_page = McpWorkspace(self.theme, McpStore())
        self._pages["mcp"] = mcp_page
        self._stack.addWidget(mcp_page)

        from beehive.app.shell.gateway_view import create_gateway_page
        gateway_page = create_gateway_page(self.theme, self._gateway_manager)
        self._pages["gateway"] = gateway_page
        self._stack.addWidget(gateway_page)

        self._subnav.select("model_config")

    def _switch_page(self, nav_id: str):
        page = self._pages.get(nav_id)
        if page:
            self._stack.setCurrentWidget(page)
