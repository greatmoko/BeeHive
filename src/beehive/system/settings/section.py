"""读取既有配置段，源码归属调整不改变磁盘键名。"""

from beehive.system.settings.config import Config


class SettingsSection:
    def __init__(self, config: Config | None = None, section: str = "wokbee"):
        self._config = config or Config()
        self._section = section

    def get(self, key: str, default=None):
        return self._config.get(f"{self._section}.{key}", default)

    def set(self, key: str, value) -> None:
        self._config.set(f"{self._section}.{key}", value)

    def save(self) -> None:
        self._config.save()
