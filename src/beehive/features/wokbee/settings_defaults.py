"""业务默认值与兼容初始化表，系统默认值来自系统设置模块。"""
from pathlib import Path
from beehive.system.settings.runtime_settings import SYSTEM_DEFAULTS
DEFAULT_WORKSPACE = Path.home() / "BeeHive" / "WokBee"

DEFAULT_APPROVAL = {
    "skip_read": True,
    "skip_write": False,
    "skip_routine": False,
    "skip_high_risk": False,
    "bypass_sandbox": False,
}

BUSINESS_DEFAULTS = {
    'workspace_root': str(DEFAULT_WORKSPACE),
    'approval': dict(DEFAULT_APPROVAL),
    'dezibee_approval': dict(DEFAULT_APPROVAL),
    'default_provider': '',
    'default_model_id': '',
    'run_max_steps': 128,
    'chat_max_steps': 64,
    'max_parallel_tools': 4,
    'dezibee_work_root': str(Path.home() / 'BeeHive' / 'DeziBee')
}
DEFAULTS = {**BUSINESS_DEFAULTS, **SYSTEM_DEFAULTS}
