"""运行环境缓存：负责设置持久化、进程内缓存和探测生命周期。"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from wokbee.engine.runtime_env_model import RuntimeEnv
from wokbee.engine.runtime_env_probe import _do_probe_runtime_env

logger = logging.getLogger("wokbee")
_mem_cache: RuntimeEnv | None = None
_probe_lock = threading.Lock()

def _settings_or_default(settings=None):
    if settings is not None:
        return settings
    from wokbee.core.settings import WokBeeSettings

    return WokBeeSettings()


def _is_valid_cached(raw) -> bool:
    return isinstance(raw, dict) and bool(str(raw.get("python_exe") or "").strip())


def get_runtime_env(settings=None) -> RuntimeEnv | None:
    """读取已缓存的本机环境（内存 → config.json），无缓存返回 None。"""
    global _mem_cache
    if _mem_cache is not None:
        return _mem_cache

    settings = _settings_or_default(settings)
    raw = settings.get("runtime_env")
    if not _is_valid_cached(raw):
        return None

    _mem_cache = RuntimeEnv.from_dict(raw)
    return _mem_cache


def save_runtime_env(settings, env: RuntimeEnv) -> None:
    """写入 config 并更新进程内缓存。"""
    global _mem_cache
    if not env.probed_at:
        env.probed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    settings.set("runtime_env", env.to_dict())
    settings.save()
    _mem_cache = env


def ensure_runtime_env(settings=None, *, force: bool = False) -> RuntimeEnv:
    """环境为空时探测并保存；已有缓存则直接返回。"""
    settings = _settings_or_default(settings)
    if not force:
        cached = get_runtime_env(settings)
        if cached is not None:
            return cached

    with _probe_lock:
        if not force:
            cached = get_runtime_env(settings)
            if cached is not None:
                return cached
        logger.info("本机运行环境为空或强制刷新，开始探测…")
        env = _do_probe_runtime_env()
        save_runtime_env(settings, env)
        logger.info("本机运行环境已保存（%s）", env.probed_at)
        return env


def ensure_runtime_env_async(
    settings=None,
    *,
    force: bool = False,
    on_done: Callable[[RuntimeEnv], None] | None = None,
) -> bool:
    """后台探测；若已有缓存且非 force 则跳过。返回是否启动了后台任务。"""
    settings = _settings_or_default(settings)
    if not force and get_runtime_env(settings) is not None:
        return False

    def _worker() -> None:
        try:
            env = ensure_runtime_env(settings, force=force)
            if on_done:
                on_done(env)
        except Exception:
            logger.exception("后台探测本机运行环境失败")

    threading.Thread(target=_worker, daemon=True, name="wokbee-runtime-env").start()
    return True


def collect_runtime_env(*, project_root: str | Path | None = None, settings=None) -> RuntimeEnv:
    """Agent 用：加载缓存环境并叠加项目目录；无缓存时同步探测一次。"""
    base = ensure_runtime_env(settings)
    return base.with_project_root(project_root)

