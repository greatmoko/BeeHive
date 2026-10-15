"""按职责组织的 settings_navigation。"""
from __future__ import annotations
from beehive.common.ui.theme import Theme
from beehive.app.shell.subnav import _SubNav


class _SettingsSubNav(_SubNav):
    ITEMS = [
        ("general", "⚙", "通用设置"),
    ]

    def __init__(self, theme: Theme, parent=None):
        super().__init__(theme, object_name="settingsSubNav", parent=parent)


_SubNav = _SettingsSubNav
