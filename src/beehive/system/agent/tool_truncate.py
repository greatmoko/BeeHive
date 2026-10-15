"""工具运行控制装配与返回形状适配。"""
from __future__ import annotations
import inspect
import logging
from typing import Any, Callable
from beehive.system.agent.tool_execution import ToolConcurrencyLimiter, _as_sync_bridge
from beehive.system.agent.tool_identity import tool_name_of, sort_tools_by_name, prefix_fingerprint
from beehive.system.agent.tool_timeouts import TOOL_TIMEOUT_EXEMPT, _callable_with_timeout, _inject_timeout_schema

logger = logging.getLogger("wokbee")

def wrap_tools_runtime_controls(
    tools: list,
    *,
    tool_timeout: float | None = None,
    max_parallel_tools: int | None = None,
) -> list:
    """包装工具的超时、节流和并发控制；工具结果保持原样返回。"""
    limiter = (
        ToolConcurrencyLimiter(max_parallel_tools)
        if max_parallel_tools is not None and int(max_parallel_tools) > 0
        else None
    )

    wrapped: list = []
    for tool in tools or []:
        try:
            wrapped.append(
                _wrap_one_tool(
                    tool,
                    tool_timeout=tool_timeout,
                    limiter=limiter,
                )
            )
        except Exception:
            logger.exception("包装工具失败，使用原工具：%s", tool_name_of(tool))
            wrapped.append(tool)
    return wrapped

def _wrap_one_tool(
    tool: Any,
    *,
    tool_timeout: float | None = None,
    limiter: ToolConcurrencyLimiter | None = None,
) -> Any:
    name = tool_name_of(tool)
    # 往 tools schema 注入可选 `timeout_seconds`，让模型能控制该调用超时；
    # 豁免工具不注入，避免在人机中断工具上误导模型。
    timeout_schema = (
        None
        if name in TOOL_TIMEOUT_EXEMPT
        else _inject_timeout_schema(getattr(tool, "args_schema", None))
    )
    response_format = getattr(tool, "response_format", "content") or "content"

    def _normalize_result(result: Any) -> Any:
        """保持 content_and_artifact 工具的返回形状。"""
        if isinstance(result, tuple) and len(result) == 2:
            return result
        if response_format == "content_and_artifact":
            return (result, None)
        return result

    def _hook(fn: Callable) -> Callable:
        """包装（可能异步的）原函数；异步函数保持异步，否则 ainvoke 会取回裸协程。"""
        from beehive.system.agent.ai_throttle import ai_throttle

        if inspect.iscoroutinefunction(fn):
            async def _ahooked(*args, **kwargs):
                if limiter is not None:
                    await limiter.acquire_async()
                try:
                    ai_throttle.wait()
                    return _normalize_result(await fn(*args, **kwargs))
                finally:
                    if limiter is not None:
                        limiter.release()
            return _ahooked

        def _hooked(*args, **kwargs):
            if limiter is not None:
                limiter.acquire()
            try:
                ai_throttle.wait()
                return _normalize_result(fn(*args, **kwargs))
            finally:
                if limiter is not None:
                    limiter.release()
        return _hooked

    # 异步入口（async @tool / MCP 等）必须一并包装，否则 ainvoke 走 coroutine 时会绕过运行控制
    coro = getattr(tool, "coroutine", None)
    if inspect.iscoroutinefunction(coro):
        aw = _hook(_callable_with_timeout(coro, name, tool_timeout))
        try:
            update = {"coroutine": aw, "func": _as_sync_bridge(aw)}
            if timeout_schema is not None:
                update["args_schema"] = timeout_schema
            return tool.model_copy(update=update)
        except Exception:
            try:
                object.__setattr__(tool, "coroutine", aw)
                object.__setattr__(tool, "func", _as_sync_bridge(aw))
                if timeout_schema is not None:
                    object.__setattr__(tool, "args_schema", timeout_schema)
                return tool
            except Exception:
                return tool

    # StructuredTool / BaseTool：优先包 _run / func
    if hasattr(tool, "func") and callable(getattr(tool, "func")):
        orig = tool.func
        newfunc = _hook(_callable_with_timeout(orig, name, tool_timeout))
        if inspect.iscoroutinefunction(orig) or inspect.iscoroutinefunction(newfunc):
            update = {"coroutine": newfunc, "func": _as_sync_bridge(newfunc)}
        else:
            update = {"func": newfunc}
        if timeout_schema is not None:
            update["args_schema"] = timeout_schema
        try:
            return tool.model_copy(update=update)
        except Exception:
            try:
                tool.func = newfunc  # type: ignore[attr-defined]
                if inspect.iscoroutinefunction(orig):
                    object.__setattr__(tool, "coroutine", newfunc)
                    object.__setattr__(tool, "func", _as_sync_bridge(newfunc))
                if timeout_schema is not None:
                    object.__setattr__(tool, "args_schema", timeout_schema)
                return tool
            except Exception:
                return tool

    if hasattr(tool, "_run") and callable(getattr(tool, "_run")):
        if timeout_schema is not None:
            try:
                object.__setattr__(tool, "args_schema", timeout_schema)
            except Exception:
                pass
        orig_run = tool._run
        if inspect.iscoroutinefunction(orig_run):
            # 异步 _run：优先包配套的异步 _arun（ainvoke 实际走它）
            arun = getattr(tool, "_arun", None)
            if inspect.iscoroutinefunction(arun):
                aw = _hook(_callable_with_timeout(arun, name, tool_timeout))
                try:
                    object.__setattr__(tool, "_arun", aw)
                    return tool
                except Exception:
                    pass
            elif callable(arun):
                orig_arun = arun

                hooked_arun = _hook(orig_arun)
                try:
                    object.__setattr__(tool, "_arun", hooked_arun)
                    return tool
                except Exception:
                    pass
            return tool

        hooked_run = _hook(_callable_with_timeout(orig_run, name, tool_timeout))
        try:
            object.__setattr__(tool, "_run", hooked_run)
        except Exception:
            try:
                tool._run = hooked_run  # type: ignore[method-assign]
            except Exception:
                return tool

        # 同步 _run 分支同样包装配套的异步 _arun：ainvoke 优先走 _arun，
        # 若只包 _run，自定义 async 工具经 ainvoke 会绕过超时/并发控制。
        arun = getattr(tool, "_arun", None)
        if inspect.iscoroutinefunction(arun):
            aw = _hook(_callable_with_timeout(arun, name, tool_timeout))
            try:
                object.__setattr__(tool, "_arun", aw)
            except Exception:
                try:
                    tool._arun = aw  # type: ignore[method-assign]
                except Exception:
                    pass
        return tool

    return tool
