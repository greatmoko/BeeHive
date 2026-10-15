"""WokBee/DeziBee 业务设置，系统属性保留明确的兼容委托。"""
from __future__ import annotations
from pathlib import Path
from beehive.system.settings.config import Config
from beehive.system.settings.runtime_settings import RuntimeSettings
from beehive.features.wokbee.projects.models import ApprovalFlags
from beehive.features.wokbee.settings_defaults import DEFAULTS, DEFAULT_APPROVAL, DEFAULT_WORKSPACE

def _default_dezibee_work_root_str() -> str:
    """默认 DeziBee 工作文件夹，与应用配置目录分开。"""
    p = Path.home() / "BeeHive" / "DeziBee"
    return str(p)


def _default_workspace_str() -> str:
    return str(DEFAULT_WORKSPACE)


class WokBeeSettings:
    def __init__(self, config: Config | None = None):
        self._config = config or Config()
        self.system = RuntimeSettings(self._config)
        self._ensure_defaults()

    def _ensure_defaults(self) -> None:
        dirty = False
        for key, value in DEFAULTS.items():
            if self._config.get(f"wokbee.{key}") is None:
                self._config.set(
                    f"wokbee.{key}",
                    dict(value) if isinstance(value, dict) else value,
                )
                dirty = True
        # 迁移旧策略字段 → approval 勾选
        if self._config.get("wokbee.approval") is None or not isinstance(
            self._config.get("wokbee.approval"), dict
        ):
            legacy = self._migrate_legacy_approval()
            self._config.set("wokbee.approval", legacy.to_dict())
            dirty = True
        else:
            # 补齐缺失的勾选键
            cur = dict(self._config.get("wokbee.approval") or {})
            for k, v in DEFAULT_APPROVAL.items():
                if k not in cur:
                    cur[k] = v
                    dirty = True
            self._config.set("wokbee.approval", cur)
        if dirty:
            self._config.save()

    def _migrate_legacy_approval(self) -> ApprovalFlags:
        raw = {
            "policy": self._config.get("wokbee.default_policy"),
            "trust_yolo": self._config.get("wokbee.allow_project_yolo"),
        }
        # allow_project_yolo 不是 trust，只表示允许；旧默认仍用 graded
        if raw.get("policy"):
            return ApprovalFlags.from_legacy(
                {"policy": raw["policy"], "trust_yolo": False}
            )
        return ApprovalFlags.from_dict(DEFAULT_APPROVAL)

    def get(self, key: str, default=None):
        return self._config.get(
            f"wokbee.{key}",
            default if default is not None else DEFAULTS.get(key),
        )

    def set(self, key: str, value) -> None:
        self._config.set(f"wokbee.{key}", value)

    def save(self) -> None:
        self._config.save()

    @property
    def workspace_root(self) -> Path:
        raw = self.get("workspace_root") or ""
        if not str(raw).strip():
            raw = _default_workspace_str()
        return Path(str(raw)).expanduser()

    @workspace_root.setter
    def workspace_root(self, value: str | Path) -> None:
        self.set("workspace_root", str(value))

    @property
    def dezibee_work_root(self) -> Path:
        raw = self.get("dezibee_work_root") or ""
        if not str(raw).strip():
            raw = _default_dezibee_work_root_str()
        return Path(str(raw)).expanduser()

    @dezibee_work_root.setter
    def dezibee_work_root(self, value: str | Path) -> None:
        self.set("dezibee_work_root", str(value))

    @property
    def approval(self) -> ApprovalFlags:
        raw = self.get("approval") or {}
        if not isinstance(raw, dict):
            return ApprovalFlags.from_dict(DEFAULT_APPROVAL)
        return ApprovalFlags.from_dict(raw)

    @approval.setter
    def approval(self, value: ApprovalFlags | dict) -> None:
        if isinstance(value, ApprovalFlags):
            self.set("approval", value.to_dict())
        else:
            self.set("approval", ApprovalFlags.from_dict(value).to_dict())

    @property
    def dezibee_approval(self) -> ApprovalFlags:
        raw = self.get("dezibee_approval")
        return ApprovalFlags.from_dict(raw if isinstance(raw, dict) else DEFAULT_APPROVAL)

    @dezibee_approval.setter
    def dezibee_approval(self, value: ApprovalFlags) -> None:
        self.set("dezibee_approval", value.to_dict())

    @property
    def default_provider(self) -> str:
        return str(self.get("default_provider") or "")

    @default_provider.setter
    def default_provider(self, value: str) -> None:
        self.set("default_provider", value or "")

    @property
    def default_model_id(self) -> str:
        return str(self.get("default_model_id") or "")

    @default_model_id.setter
    def default_model_id(self, value: str) -> None:
        self.set("default_model_id", value or "")

    @property
    def run_max_steps(self) -> int:
        try:
            return max(1, min(500, int(self.get("run_max_steps", 128))))
        except (TypeError, ValueError):
            return 128

    @run_max_steps.setter
    def run_max_steps(self, value: int) -> None:
        self.set("run_max_steps", max(1, min(500, int(value))))

    @property
    def max_parallel_tools(self) -> int:
        try:
            return max(1, min(16, int(self.get("max_parallel_tools", 4))))
        except (TypeError, ValueError):
            return 4

    @max_parallel_tools.setter
    def max_parallel_tools(self, value: int) -> None:
        self.set("max_parallel_tools", max(1, min(16, int(value))))

    @property
    def chat_max_steps(self) -> int:
        try:
            return max(1, min(500, int(self.get("chat_max_steps", 64))))
        except (TypeError, ValueError):
            return 64

    @chat_max_steps.setter
    def chat_max_steps(self, value: int) -> None:
        self.set("chat_max_steps", max(1, min(500, int(value))))

    @property
    def additional_directories(self):
        return self.system.directories.additional_directories

    @additional_directories.setter
    def additional_directories(self, value):
        self.system.directories.additional_directories = value

    @property
    def ai_interval_ms(self):
        return self.system.ai_interval_ms

    @ai_interval_ms.setter
    def ai_interval_ms(self, value):
        self.system.ai_interval_ms = value

    @property
    def enable_deepseek_search(self):
        return self.system.enable_deepseek_search

    @enable_deepseek_search.setter
    def enable_deepseek_search(self, value):
        self.system.enable_deepseek_search = value

    @property
    def model_timeout_seconds(self):
        return self.system.model_timeout_seconds

    @model_timeout_seconds.setter
    def model_timeout_seconds(self, value):
        self.system.model_timeout_seconds = value

    @property
    def skills_dirs(self):
        return self.system.directories.skills_dirs

    @skills_dirs.setter
    def skills_dirs(self, value):
        self.system.directories.skills_dirs = value

    @property
    def terminal_app(self):
        return self.system.terminal_app

    @terminal_app.setter
    def terminal_app(self, value):
        self.system.terminal_app = value

    @property
    def tool_timeout_seconds(self):
        return self.system.tool_timeout_seconds

    @tool_timeout_seconds.setter
    def tool_timeout_seconds(self, value):
        self.system.tool_timeout_seconds = value

    def add_additional_directory(self, name, path, slug=""):
        return self.system.directories.add_additional_directory(name, path, slug)

    def remove_additional_directory(self, path):
        return self.system.directories.remove_additional_directory(path)

    def add_skills_dir(self, path):
        return self.system.directories.add_skills_dir(path)

    def remove_skills_dir(self, path):
        return self.system.directories.remove_skills_dir(path)
