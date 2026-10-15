"""按职责组织的 configuration_navigation。"""
from __future__ import annotations
from beehive.common.ui.theme import Theme
from beehive.app.shell.subnav import _SubNav


class _ConfigSubNav(_SubNav):
    ITEMS = [
        ("model_config", "🧠", "厂商设置"),
        ("ai_roles", "🤖", "AI 角色"),
        ("session_defaults", "⚙", "TokBee 设置"),
        ("system_settings", "🖥", "系统运行设置"),
        ("wokbee_settings", "🐝", "WokBee 设置"),
        ("dezibee_settings", "🎨", "DeziBee 设置"),
        ("global_memory", "🧠", "全局记忆"),
        ("atomic_memory", "🧩", "原子记忆"),
        ("skills", "📚", "Skills"),
        ("credentials", "🔑", "凭据库"),
        ("mcp", "🔌", "MCP"),
        ("gateway", "📡", "消息网关"),
    ]

    def __init__(self, theme: Theme, parent=None):
        super().__init__(theme, object_name="aiConfigSubNav", parent=parent)


_SubNav = _ConfigSubNav
