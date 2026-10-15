"""execute 外层墙钟等待、取消和独立共享监视池。"""
from __future__ import annotations
import time
from deepagents.backends.protocol import ExecuteResponse

_EXEC_WATCH = None


def _exec_watch_pool():
    global _EXEC_WATCH
    if _EXEC_WATCH is None:
        from concurrent.futures import ThreadPoolExecutor

        _EXEC_WATCH = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="wokbee-exec-watch"
        )
    return _EXEC_WATCH


def attach_execute_watch(backend, *, cancel_event, default_timeout: float) -> None:
    """给 backend.execute 再套一层墙钟等待：内层哪怕卡死，外层也会在超时/暂停时返回。

    LangGraph 调的是最外层 backend（AccessCoerceBackend）。仅改 ArchiveDeniedBackend
    时，若调用链没走进去或 communicate 卡死，UI 仍会永远「调用中」。
    """
    from concurrent.futures import TimeoutError as FutTimeout
    from beehive.common.process.subprocess_util import kill_all_cancellable_runs

    inner = backend.execute
    default = max(1.0, float(default_timeout or 90))

    def execute(command: str, *, timeout: int | None = None):
        cap = float(timeout) if timeout is not None and float(timeout) > 0 else default
        fut = _exec_watch_pool().submit(inner, command, timeout=timeout)
        deadline = time.monotonic() + cap
        while True:
            if cancel_event is not None and cancel_event.is_set():
                kill_all_cancellable_runs(cancel_event=cancel_event)
                return ExecuteResponse(
                    output="命令已取消：用户点击了暂停，进程树已被终止。",
                    exit_code=130,
                    truncated=False,
                )
            left = deadline - time.monotonic()
            if left <= 0:
                kill_all_cancellable_runs(cancel_event=cancel_event)
                return ExecuteResponse(
                    output=(
                        f"Error: Command timed out after {cap:g} seconds "
                        "(watchdog). The command may be stuck or require more time."
                    ),
                    exit_code=124,
                    truncated=False,
                )
            try:
                return fut.result(timeout=min(0.25, max(0.05, left)))
            except FutTimeout:
                continue

    backend.execute = execute  # type: ignore[method-assign]
