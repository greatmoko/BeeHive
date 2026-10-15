"""系统运行设置；继续读写历史 wokbee 配置段，不在构造时写业务默认值。"""
from beehive.system.settings.section import SettingsSection
from beehive.system.settings.directory_settings import DirectorySettings
from beehive.system.settings.terminal_apps import _DEFAULT_TERMINAL

SYSTEM_DEFAULTS = {
    'approval_timeout_seconds': 12 * 60 * 60,

    'ai_interval_ms': 0,

    'enable_deepseek_search': True,

    'tool_timeout_seconds': 90,

    'model_timeout_seconds': 180,

    'additional_directories': [],

    'skills_dirs': [],

    'terminal_app': ''
}

class RuntimeSettings(SettingsSection):
    def __init__(self, config=None):
        super().__init__(config)
        self.directories = DirectorySettings(self._config)

    def get(self, key, default=None):
        return super().get(key, default if default is not None else SYSTEM_DEFAULTS.get(key))

    @property
    def enable_deepseek_search(self) -> bool:
        return bool(self.get("enable_deepseek_search", True))

    @enable_deepseek_search.setter
    def enable_deepseek_search(self, value: bool) -> None:
        self.set("enable_deepseek_search", bool(value))

    @property
    def terminal_app(self) -> str:
        """终端运行软件；未选择（空）时回退 cmd。"""
        raw = str(self.get("terminal_app") or "").strip().lower()
        return raw or _DEFAULT_TERMINAL

    @terminal_app.setter
    def terminal_app(self, value: str) -> None:
        self.set("terminal_app", str(value or "").strip().lower())

    @property
    def ai_interval_ms(self) -> int:
        try:
            return max(0, min(60000, int(self.get("ai_interval_ms", 0) or 0)))
        except (TypeError, ValueError):
            return 0

    @ai_interval_ms.setter
    def ai_interval_ms(self, value: int) -> None:
        self.set("ai_interval_ms", max(0, min(60000, int(value or 0))))

    @property
    def tool_timeout_seconds(self) -> int:
        try:
            return max(5, int(self.get("tool_timeout_seconds", 90) or 90))
        except (TypeError, ValueError):
            return 90

    @tool_timeout_seconds.setter
    def tool_timeout_seconds(self, value: int) -> None:
        self.set("tool_timeout_seconds", max(5, min(3600, int(value))))

    @property
    def model_timeout_seconds(self) -> int:
        try:
            return max(10, int(self.get("model_timeout_seconds", 180) or 180))
        except (TypeError, ValueError):
            return 180

    @model_timeout_seconds.setter
    def model_timeout_seconds(self, value: int) -> None:
        self.set("model_timeout_seconds", max(10, min(3600, int(value))))
