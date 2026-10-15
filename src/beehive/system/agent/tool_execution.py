"""工具同步/异步桥接、进程共享执行池和单图并发限制。"""
from __future__ import annotations
import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

# 供同步工具超时使用的共享线程池（进程级、跨运行复用）。
_TOOL_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(8, min(16, (os.cpu_count() or 2) + 4)),
    thread_name_prefix="wokbee-tool",
)



class ToolConcurrencyLimiter:
    """限制同一 Agent 执行图的工具体并发。"""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self._semaphore = threading.BoundedSemaphore(self.limit)

    def acquire(self) -> None:
        self._semaphore.acquire()

    def release(self) -> None:
        self._semaphore.release()

    async def acquire_async(self) -> None:
        # 轮询信号量以避免阻塞事件循环，取消时不会泄漏许可。
        while not self._semaphore.acquire(blocking=False):
            await asyncio.sleep(0.01)

def _run_async_fn_sync(async_fn: Callable, *args, **kwargs):
    """在同步 Agent（stream/invoke）里跑只提供 coroutine 的工具（MCP 等）。"""

    def _call():
        return asyncio.run(async_fn(*args, **kwargs))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _call()
    # 已在事件循环中：不能再 asyncio.run，丢到旁路线程
    fut = _TOOL_EXECUTOR.submit(_call)
    return fut.result()

def _as_sync_bridge(async_fn: Callable) -> Callable:
    def _sync(*args, **kwargs):
        return _run_async_fn_sync(async_fn, *args, **kwargs)

    _sync.__name__ = getattr(async_fn, "__name__", "_sync")
    return _sync
