"""运行环境兼容入口。

实现按模型、探测、缓存和格式化职责拆分；本模块保留旧导入路径。
"""

from wokbee.engine.runtime_env_cache import (
    _is_valid_cached,
    _mem_cache,
    _probe_lock,
    _settings_or_default,
    collect_runtime_env,
    ensure_runtime_env,
    ensure_runtime_env_async,
    get_runtime_env,
    save_runtime_env,
)
from wokbee.engine.runtime_env_format import (
    _format_tool_line,
    build_execute_invocation,
    build_runtime_env_block,
    build_runtime_env_settings_text,
    enrich_shell_env,
    format_runtime_env_block,
)
from wokbee.engine.runtime_env_model import RuntimeEnv
from wokbee.engine.runtime_env_probe import (
    _CLI_TOOLS,
    _do_probe_runtime_env,
    _probe_cli_tools,
    _run_version,
    _which,
)

__all__ = [
    "RuntimeEnv",
    "collect_runtime_env",
    "ensure_runtime_env",
    "ensure_runtime_env_async",
    "get_runtime_env",
    "save_runtime_env",
    "enrich_shell_env",
    "format_runtime_env_block",
    "build_runtime_env_block",
    "build_runtime_env_settings_text",
    "build_execute_invocation",
]
