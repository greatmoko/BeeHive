"""工具调用超时参数、schema 注入与超时包装。"""
from __future__ import annotations
import asyncio
import inspect
from concurrent.futures import TimeoutError as _FutureTimeout
from typing import Any, Callable, Optional
from pydantic import BaseModel, Field, create_model
from beehive.system.agent.tool_execution import _TOOL_EXECUTOR

# 单个工具执行超时：超过时限立即返回失败结果，交给 Agent 接管。
# 人在环工具（ask_user 在工具体内 interrupt()）与子代理工具（task）不套硬超时，
# 否则会吞掉 graph interrupt 或误杀长时间合法的子代理运行。
TOOL_TIMEOUT_EXEMPT = frozenset({
    "ask_user", "task", "request_access",
    # Python future 无法中止进行中的文件写入，提前返回超时会让重试与原写入竞争。
    "write_file_chunk", "insert_text",
})

# AI 逐调用覆盖默认超时的保留参数名：调用工具时传 `timeout_seconds`，未传则用全局默认值。
PER_CALL_TIMEOUT_KEY = "timeout_seconds"


def tool_timeout_result(tool_name: str, timeout: float) -> str:
    return (
        f"工具 `{tool_name}` 执行超时（> {timeout:g} 秒），已被终止并交由 AI 接管。"
        "请拆成更小的步骤，或改用更轻量的替代方式。"
    )

def _resolve_tool_timeout(kwargs: dict, default_timeout: float | None) -> float | None:
    """从调用 kwargs 里读出该次调用的生效超时（秒）。

    规则：AI 显式传 `timeout_seconds` 则优先生效（>0 用之；<=0 视为"不限定"，返回 None）；
    传了但非法（非数字）回落默认；未传则回落默认；默认 <=0 表示禁用超时。
    同时把 `timeout_seconds` 从 kwargs 弹出，避免透传给真正的工具函数（其签名不认识它）。
    """
    base = None if (default_timeout is None or default_timeout <= 0) else float(default_timeout)
    per_call = kwargs.pop(PER_CALL_TIMEOUT_KEY, None)
    if per_call is None:
        return base
    try:
        value = float(per_call)
    except (TypeError, ValueError):
        return base
    return value if value > 0 else None

def _inject_timeout_schema(schema: Any) -> Any:
    """给工具 args_schema 注入可选的 `timeout_seconds` 字段，让模型看得到、传得了。

    通过为原 schema 生成一个带新增字段的子类实现；若 schema 不是 pydantic BaseModel、
    或注入失败，则原样返回（优雅降级：该工具无法用 timeout_seconds，仅影响覆盖面）。
    """
    if schema is None or not (isinstance(schema, type) and issubclass(schema, BaseModel)):
        return schema
    if PER_CALL_TIMEOUT_KEY in getattr(schema, "model_fields", {}):
        return schema
    try:
        return create_model(
            f"{schema.__name__}_WokBeeTimeout",
            __base__=schema,
            timeout_seconds=(
                Optional[float],
                Field(
                    default=None,
                    ge=1,
                    description=(
                        "覆盖此工具调用的默认超时（秒）。下载/长时任务可传较大值以延长等待，"
                        "未传则用全局默认。"
                    ),
                ),
            ),
        )
    except Exception:  # noqa: BLE001
        return schema

def _callable_with_timeout(
    fn: Callable, tool_name: str, default_timeout: float | None
) -> Callable:
    """给工具执行挂上超时：超时返回失败结果，其余异常照常抛出。

    `default_timeout` 是全局默认；AI 可在调用时传 `timeout_seconds` 覆盖（见
    `_resolve_tool_timeout`），仅当两者都未提供/无效时才不超时。

    - 异步 coroutine 用 asyncio.wait_for，可真正取消。
    - 同步函数用共享线程池跑 + result(timeout)：超时后主线程返回失败。
      被放弃的线程会继续到其自身收尾（execute/网络工具自带子进程与连接超时，
      因而不会被无限占用）；纯 Python 线程无法在进程内强杀，此为已知边界。
    """
    if tool_name in TOOL_TIMEOUT_EXEMPT:
        return fn
    if inspect.iscoroutinefunction(fn):
        async def _aw(*args, **kwargs):
            timeout = _resolve_tool_timeout(kwargs, default_timeout)
            if not timeout:
                return await fn(*args, **kwargs)
            try:
                return await asyncio.wait_for(fn(*args, **kwargs), timeout=timeout)
            except asyncio.TimeoutError:
                return tool_timeout_result(tool_name, timeout)
            except asyncio.CancelledError:
                return tool_timeout_result(tool_name, timeout)

        _aw.__name__ = getattr(fn, "__name__", "_aw")
        return _aw

    def _sw(*args, **kwargs):
        timeout = _resolve_tool_timeout(kwargs, default_timeout)
        if not timeout:
            return fn(*args, **kwargs)
        future = _TOOL_EXECUTOR.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except _FutureTimeout:
            return tool_timeout_result(tool_name, timeout)

    _sw.__name__ = getattr(fn, "__name__", "_sw")
    return _sw
