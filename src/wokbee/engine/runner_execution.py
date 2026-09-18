"""Deep Agents 运行器：目标 → 执行 → 审批门 → lesson。"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from deepagents import FilesystemMiddleware, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend
from langgraph.errors import GraphInterrupt, GraphRecursionError
from langgraph.types import Command

from tokbee.core.provider_store import ProviderStore, ResolvedModel

from wokbee.core.models import Project, _now
from wokbee.core.timeline_format import (
    format_tool_call_for_timeline,
    format_tool_callback_for_timeline,
)
from wokbee.core.credential_store import redact_obj, redact_text
from wokbee.core.settings import WokBeeSettings
from wokbee.engine.approval_policy import (
    build_interrupt_on,
    risk_label_for_tool,
)
from wokbee.engine.archive_guard import ArchiveDeniedBackend, attach_execute_watch
from wokbee.engine.access_request import (
    ApprovedDirRegistry,
    build_access_request_tool,
    mount_dir,
)
from wokbee.engine.access_coerce import AccessCoerceBackend
from wokbee.engine.readonly_backend import ReadOnlyBackend
from wokbee.engine.file_tools import FILESYSTEM_TOOL_DESCRIPTIONS, build_file_tools
from wokbee.engine.lessons import (
    Lesson,
    LessonStore,
    build_lesson_digest,
    build_experience_tools,
    collect_events_log,
    collect_scripts_context,
    slice_latest_round,
    summarize_lesson_with_ai,
    sync_lesson_from_pipeline,
)
from wokbee.engine.runtime_env import build_runtime_env_block
from wokbee.engine.model_factory import build_chat_model
from wokbee.engine.network_tools import NETWORK_TOOLS
from wokbee.engine.cache_prefix import (
    CacheHitTracker,
    PrefixGuard,
    build_session_context_block,
    compose_user_with_context,
    prefix_fingerprint,
    sort_tools_by_name,
    static_system_prompt,
    tool_name_of,
    wrap_tools_truncate_results,
)
from wokbee.engine.ask_user import (
    build_ask_user_tool,
    is_ask_user_interrupt,
    normalize_ask_user_value,
)
from wokbee.engine.project_tools import build_project_meta_tools
from wokbee.engine.credential_tools import build_credential_tools
from wokbee.engine.autobee_tools import build_autobee_tools
from wokbee.engine.script_factory import (
    apply_ai_authored_scripts,
    apply_ai_pipeline_steps,
    drop_missing_pipeline_scripts,
    solidify_scripts,
)
from wokbee.engine.script_runner import (
    build_user_message_for_ai_phase,
    peek_pipeline,
    publish_pipeline_outputs,
    run_pipeline_until_ai_or_end,
)
from wokbee.core.skills_store import SkillsStore
from wokbee.core.mcp_store import McpStore
from wokbee.engine.runner_events import EventCallback, ExecutionEventLog
from wokbee.engine.runner_models import RunRequest, RunResult, StepBudget, StepLimitExceeded
from wokbee.engine.runner_sessions import (
    get_checkpointer as _get_checkpointer,
    remember_agent,
    reset_run_state as _reset_run_state,
)
from wokbee.engine.runner_assembly import (
    configure_design_write_validator,
    prepare_project_root,
    resolve_model_for_project,
)
from wokbee.engine.runner_experience import (
    fallback_notes,
    fallback_success_path,
    load_latest_round_events,
)
from wokbee.engine.runner_experience_writer import ExperienceWriterMixin
from wokbee.engine.runner_agent_assembly import AgentAssemblyMixin
from wokbee.engine.runner_flow import RunnerFlowMixin

logger = logging.getLogger("wokbee")

ApprovalCallback = Callable[[list[dict]], None]  # pending action summaries
_StepBudget = StepBudget  # 私有旧名兼容

# 【会话上下文】块的固定首行，用作「是否已注入」的稳定哨兵（内容可随项目变更，
# 但首行字面量恒定）。判定已注入时以此为准，不依赖 project.title/goal 等易变内容。
_CONTEXT_SENTINEL = "【会话上下文】"


def _format_engine_error(exc: BaseException) -> str:
    """把厂商网关错误翻成可读说明，避免和本机 MCP 调用失败混在一起。"""
    text = str(exc)
    low = text.lower()

    # 已有明确可读原始信息时优先（网关/MCP 特判），避免被分类覆盖。
    if (
        "do_request_failed" in low
        or "upstream error" in low
        or ("error code: 500" in low and "new_api" in low)
    ):
        return (
            "模型网关返回 500（上游请求失败）。这是厂商把请求转到 DeepSeek 时失败，"
            "发生在模型 HTTP 调用阶段，不是 MCP 工具执行失败。"
            "请稍后重试；若连续出现，可暂时关掉部分 MCP 减少 tools 数量，"
            "或到网关后台用 request id 查日志。"
            f"\n原始信息：{text}"
        )
    if "does not support sync invocation" in low:
        return (
            "MCP 工具缺少同步入口。请确认已更新并重启应用后再试。"
            f"\n原始信息：{text}"
        )

    # 统一分类兜底：命中已知类时给出针对性诊断/建议。
    try:
        from wokbee.engine.ai_errors import (
            AIErrorKind,
            classify_error,
            display_message,
        )

        kind = classify_error(exc)
        if kind is not AIErrorKind.UNKNOWN:
            # 可自动重试类：deepagents / max_retries 已做底层重试，提示稍后重试；
            # 不可重试类：给出定位与修正建议。
            return display_message(kind, text)
    except Exception:
        pass
    return text




def _attachment_content(text: str, attachments: list[dict] | None) -> Any:
    """将上传附件附加到用户消息；图片使用 OpenAI image_url，多媒体文件给出 uploads 路径。"""
    attachments = attachments or []
    images: list[dict] = []
    files: list[dict] = []
    for item in attachments:
        if not isinstance(item, dict):
            continue
        path = Path(str(item.get("path") or ""))
        if not path.exists() or not path.is_file():
            continue
        if str(item.get("kind") or "") == "image":
            images.append(item)
        else:
            files.append(item)

    file_note = ""
    if files:
        names = []
        for item in files:
            path = Path(str(item.get("path") or ""))
            names.append(f"uploads/{path.name}")
        file_note = "\n\n[附加文件（已保存到项目 uploads/，可用文件工具读取）]\n" + "\n".join(
            f"- {name}" for name in names
        )

    text = (text or "") + file_note
    if not images:
        return text

    parts: list[dict] = []
    if text:
        parts.append({"type": "text", "text": text})
    for item in images:
        path = Path(str(item.get("path") or ""))
        try:
            raw = path.read_bytes()
            mime = str(item.get("mime") or "")
            if not mime:
                import mimetypes
                mime = mimetypes.guess_type(path.name)[0] or "image/png"
            encoded = base64.b64encode(raw).decode("ascii")
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{encoded}"},
            })
        except OSError:
            parts.append({"type": "text", "text": f"[无法读取图片附件：{path.name}]"})
    return parts or text


def _message_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                # 兼容 text / output_text 等块
                if block.get("type") in ("text", "output_text", "input_text") or "text" in block:
                    parts.append(str(block.get("text") or ""))
                elif block.get("type") == "reasoning":
                    continue
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts).strip()
    return str(content).strip()


def _reasoning_text(msg: Any) -> str:
    """读取厂商附带的思考/推理文本（如 DeepSeek 的 reasoning_content）。

    标准 ChatOpenAI 解析流式 delta 时丢弃该字段，须先经模型层保留才有值；否则恒空。
    """
    ak = getattr(msg, "additional_kwargs", None)
    if isinstance(msg, dict):
        ak = msg.get("additional_kwargs")
    if not isinstance(ak, dict):
        return ""
    rc = ak.get("reasoning_content") or ""
    if isinstance(rc, list):
        rc = "".join(str(x) for x in rc)
    return str(rc).strip()


def _raw_text(content: Any) -> str:
    """保留空白地抽取消息文本增量（流式 delta 用，勿 strip 以免吞词间空格）。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") in ("text", "output_text", "input_text") or "text" in b:
                    parts.append(str(b.get("text") or ""))
                elif b.get("type") == "reasoning":
                    continue
            elif isinstance(b, str):
                parts.append(b)
        return "".join(parts)
    return str(content)


def _raw_reasoning(chunk: Any) -> str:
    """读取厂商附带的思考 delta（保留空白）。"""
    ak = getattr(chunk, "additional_kwargs", None)
    if isinstance(chunk, dict):
        ak = chunk.get("additional_kwargs")
    if not isinstance(ak, dict):
        return ""
    rc = ak.get("reasoning_content") or ""
    if isinstance(rc, list):
        rc = "".join(str(x) for x in rc)
    return str(rc)


def _stream_delta_parts(chunk: Any) -> tuple[str, str]:
    """把单个流式消息块拆成 (reasoning_delta, text_delta)。"""
    try:
        return _raw_reasoning(chunk), _raw_text(
            getattr(chunk, "content", None) if not isinstance(chunk, dict) else chunk.get("content")
        )
    except Exception:
        return "", ""


def _is_ai_message(msg: Any) -> bool:
    cls = msg.__class__.__name__ if not isinstance(msg, dict) else str(msg.get("type") or "")
    role = getattr(msg, "type", None) or (
        msg.get("role") if isinstance(msg, dict) else None
    ) or cls
    role_s = str(role or "")
    return "AI" in cls or role_s in ("ai", "assistant", "AIMessage")


def _extract_text(messages: list) -> str:
    """取最近一条**非空** AI 正文（跳过空 content / Tool / Human）。"""
    if not messages:
        return ""
    for msg in reversed(list(messages)):
        if not _is_ai_message(msg):
            continue
        content = (
            getattr(msg, "content", None)
            if not isinstance(msg, dict)
            else msg.get("content")
        )
        text = _message_text(content)
        if text:
            return text
        rc = _reasoning_text(msg)
        if rc:
            return rc
    return ""


def _msg_id(msg: Any) -> str:
    mid = getattr(msg, "id", None)
    if mid:
        return str(mid)
    if isinstance(msg, dict) and msg.get("id"):
        return str(msg["id"])
    return ""


def _msg_fingerprint(msg: Any) -> str:
    """稳定去重键：优先消息 id / tool_call_id，否则内容指纹。

    LangGraph stream 后若再遍历 get_state 全量 messages，无 id 时会重复写入时间线。
    """
    mid = _msg_id(msg)
    if mid:
        return f"id:{mid}"

    tcid = getattr(msg, "tool_call_id", None)
    if not tcid and isinstance(msg, dict):
        tcid = msg.get("tool_call_id")
    if tcid:
        return f"toolmsg:{tcid}"

    tc_ids: list[str] = []
    for tc in _tool_calls_of(msg):
        if isinstance(tc, dict):
            tid = str(tc.get("id") or tc.get("tool_call_id") or "").strip()
        else:
            tid = str(getattr(tc, "id", "") or getattr(tc, "tool_call_id", "") or "").strip()
        if tid:
            tc_ids.append(tid)
    if tc_ids:
        return "tc:" + ",".join(tc_ids)

    cls = msg.__class__.__name__ if not isinstance(msg, dict) else str(msg.get("type", "dict"))
    role = getattr(msg, "type", None) or (msg.get("role") if isinstance(msg, dict) else "") or cls
    name = getattr(msg, "name", None) or (msg.get("name") if isinstance(msg, dict) else "") or ""
    text = _message_text(
        getattr(msg, "content", None) if not isinstance(msg, dict) else msg.get("content")
    )[:800]
    # 含 tool_calls 摘要，避免仅文本相同的不同调用被误去重
    if _tool_calls_of(msg):
        bits = []
        for tc in _tool_calls_of(msg)[:6]:
            n, a = _tool_call_parts(tc)
            bits.append(f"{n}:{json.dumps(a, ensure_ascii=False, sort_keys=True)[:120]}")
        text = text + "|" + ";".join(bits)
    raw = f"{role}|{name}|{text}"
    import hashlib

    return "fp:" + hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:20]


def _tool_calls_of(msg: Any) -> list:
    tcs = getattr(msg, "tool_calls", None)
    if tcs:
        return list(tcs)
    if isinstance(msg, dict):
        return list(msg.get("tool_calls") or [])
    additional = getattr(msg, "additional_kwargs", None) or {}
    if isinstance(additional, dict):
        return list(additional.get("tool_calls") or [])
    return []


def _tool_call_parts(tc: Any) -> tuple[str, dict]:
    if isinstance(tc, dict):
        name = str(tc.get("name") or tc.get("function", {}).get("name") or "tool")
        args = tc.get("args")
        if args is None and isinstance(tc.get("function"), dict):
            args = tc["function"].get("arguments")
    else:
        name = str(getattr(tc, "name", "tool") or "tool")
        args = getattr(tc, "args", {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"raw": args[:500]}
    if not isinstance(args, dict):
        args = {"raw": args}
    return name, args


def _tool_call_id(tc: Any) -> str:
    """取工具调用的 id，用于 call ↔ callback 配对（兼容 dict/对象）。"""
    if isinstance(tc, dict):
        return str(tc.get("id") or tc.get("tool_call_id") or "").strip()
    return str(getattr(tc, "id", "") or getattr(tc, "tool_call_id", "") or "").strip()


def _format_tool_call(tc: Any) -> str:
    name, args = _tool_call_parts(tc)
    args = redact_obj(args)
    args_s = json.dumps(args, ensure_ascii=False)
    if len(args_s) > 400:
        args_s = args_s[:400] + "…"
    return f"{name}({args_s})"


def build_success_path_from_messages(messages: list, *, limit: int = 40) -> str:
    """从本轮消息中的工具调用轨迹提炼「成功实现路径」。"""
    steps: list[str] = []
    for msg in messages or []:
        if len(steps) >= limit:
            break
        cls = msg.__class__.__name__ if not isinstance(msg, dict) else str(msg.get("type", ""))
        role = getattr(msg, "type", None) or (msg.get("role") if isinstance(msg, dict) else "") or cls

        for tc in _tool_calls_of(msg):
            if len(steps) >= limit:
                break
            steps.append(f"{len(steps) + 1}. call: {_format_tool_call(tc)}")

        if "Tool" in cls or role in ("tool", "ToolMessage"):
            name = (
                getattr(msg, "name", None)
                or (msg.get("name") if isinstance(msg, dict) else None)
                or "tool"
            )
            body = _message_text(
                getattr(msg, "content", None) if not isinstance(msg, dict) else msg.get("content")
            )
            body = redact_text(body)
            if str(name) == "get_credential":
                body = "（凭据明文已隐藏）"
            if len(body) > 220:
                body = body[:220] + "…"
            if body:
                steps.append(f"{len(steps) + 1}. callback: {name} — {body}")
            else:
                steps.append(f"{len(steps) + 1}. callback: {name}")

    if not steps:
        return ""
    return "\n".join(steps)


def _emit_message_events(
    emit: EventCallback,
    msg: Any,
    seen: set[str],
    *,
    cache_tracker: CacheHitTracker | None = None,
) -> None:
    """把单条消息转成时间线事件（跳过已发过的 id/指纹）。"""
    key = _msg_fingerprint(msg)
    if key in seen:
        return
    seen.add(key)

    cls = msg.__class__.__name__ if not isinstance(msg, dict) else msg.get("type", "")
    role = getattr(msg, "type", None) or (msg.get("role") if isinstance(msg, dict) else "") or cls

    # ToolMessage
    if "Tool" in cls or role in ("tool", "ToolMessage"):
        name = getattr(msg, "name", None) or (msg.get("name") if isinstance(msg, dict) else "") or "tool"
        body = _message_text(
            getattr(msg, "content", None) if not isinstance(msg, dict) else msg.get("content")
        )
        tcid = getattr(msg, "tool_call_id", None)
        if not tcid and isinstance(msg, dict):
            tcid = msg.get("tool_call_id")
        status = "success"
        if isinstance(msg, dict):
            status = str(msg.get("status") or "success").lower()
        else:
            status = str(getattr(msg, "status", "success") or "success").lower()
        emit(
            "tool",
            format_tool_callback_for_timeline(str(name), body),
            {
                "tool": name,
                "phase": "callback",
                "tool_call_id": str(tcid or ""),
                "status": status,
            },
        )
        return

    # AIMessage / assistant
    if "AI" in cls or role in ("ai", "assistant", "AIMessage"):
        if cache_tracker is not None:
            try:
                cache_tracker.observe_message(msg)
            except Exception:
                logger.exception("cache hit 观测失败")
        tcs = _tool_calls_of(msg)
        text = _message_text(
            getattr(msg, "content", None) if not isinstance(msg, dict) else msg.get("content")
        )
        reasoning = _reasoning_text(msg)
        if reasoning:
            emit("agent", reasoning, {"phase": "reasoning"})
        # AI 正文总是发射：有工具调用时作为「旁白/指挥」先于 call 展示，
        # 无工具调用时作为「AI 回答」→ 顺序: reasoning → 旁白 → call1..N → callback1..N
        if text:
            emit("agent", text, {"phase": "narration" if tcs else "answer"})
        for tc in tcs:
            name, args = _tool_call_parts(tc)
            emit(
                "tool",
                format_tool_call_for_timeline(name, args),
                {
                    "phase": "call",
                    "tool": name,
                    "args": args,
                    "tool_call_id": _tool_call_id(tc),
                },
            )
        return

    # Human / other — 一般不回显用户消息（UI 已有）
    return


def _collect_messages_from_update(update: Any) -> list:
    msgs: list = []
    if isinstance(update, dict):
        if "messages" in update:
            raw = update["messages"]
            if isinstance(raw, list):
                msgs.extend(raw)
            else:
                msgs.append(raw)
        else:
            # node_name -> payload
            for v in update.values():
                if isinstance(v, dict) and "messages" in v:
                    raw = v["messages"]
                    if isinstance(raw, list):
                        msgs.extend(raw)
                    else:
                        msgs.append(raw)
                elif isinstance(v, list):
                    msgs.extend(v)
    return msgs


def _iter_interrupt_values(agent, config: dict):
    """遍历 checkpoint 中未处理的 interrupt 值。"""
    try:
        state = agent.get_state(config)
    except Exception as e:
        logger.warning("get_state 失败: %s", e)
        return
    tasks = getattr(state, "tasks", None) or ()
    for task in tasks:
        interrupts = getattr(task, "interrupts", None) or ()
        for intr in interrupts:
            yield getattr(intr, "value", intr)


def _first_ask_user_payload(agent, config: dict) -> dict | None:
    for value in _iter_interrupt_values(agent, config):
        if is_ask_user_interrupt(value):
            return normalize_ask_user_value(value)
        # 兼容：仅含 questions 的载荷
        if isinstance(value, dict) and value.get("questions") and not (
            value.get("action_requests") or value.get("actions")
        ):
            return normalize_ask_user_value({**value, "type": "ask_user"})
    return None


def _pending_from_state(agent, config: dict) -> list[dict]:
    """从 checkpoint state 解析待审批动作（跳过 ask_user 澄清中断）。"""
    pending: list[dict] = []
    for value in _iter_interrupt_values(agent, config):
        if is_ask_user_interrupt(value):
            continue
        if isinstance(value, dict) and value.get("questions") and not (
            value.get("action_requests") or value.get("actions")
        ):
            continue

        action_requests = None
        if isinstance(value, dict):
            action_requests = value.get("action_requests") or value.get("actions")
        else:
            action_requests = getattr(value, "action_requests", None)

        if not action_requests:
            # 兜底：整包当作一条（非 ask_user）
            pending.append(
                {
                    "name": "tool",
                    "args": {},
                    "description": redact_text(str(value)[:500]),
                    "risk": "操作",
                }
            )
            continue

        for action in action_requests:
            if isinstance(action, dict):
                name = action.get("name") or action.get("tool") or "tool"
                args = action.get("args") or action.get("arguments") or {}
            else:
                name = getattr(action, "name", None) or "tool"
                args = getattr(action, "args", {}) or {}
            pending.append(
                {
                    "name": str(name),
                    "args": args if isinstance(args, dict) else {"raw": str(args)},
                    "description": f"{name}({args})"[:400],
                    "risk": risk_label_for_tool(str(name)),
                }
            )
    for item in pending:
        item["args"] = redact_obj(item.get("args") or {})
        item["description"] = redact_text(str(item.get("description") or ""))
    return pending


def _has_pending(agent, config: dict) -> bool:
    try:
        state = agent.get_state(config)
        return bool(getattr(state, "next", None))
    except Exception:
        return False


class AgentRunner(AgentAssemblyMixin, ExperienceWriterMixin, RunnerFlowMixin):
    """同步运行（应在后台线程调用）。"""

    def __init__(
        self,
        settings: WokBeeSettings | None = None,
        provider_store: ProviderStore | None = None,
    ):
        self.settings = settings or WokBeeSettings()
        self.provider_store = provider_store or ProviderStore()
        self._cancel = threading.Event()
        self._approval_event = threading.Event()
        self._approval_decisions: list[dict] | None = None
        self._ask_user_event = threading.Event()
        self._ask_user_answers: dict | None = None
        self._event_log = ExecutionEventLog()
        self._cache_tracker = CacheHitTracker()
        self._prefix_guard: PrefixGuard | None = None
        self._session_context_block: str = ""
        self._context_injected: bool = False
        # 本轮是否已由 Agent 用 update_project_experience 工具更新过经验（结束兜底据此跳过）
        self._experience_updated_by_tool: bool = False
        self._step_budget: _StepBudget | None = None
        self._seen_tool_update_ids: set[str] = set()
        self._phase_states: list[dict[str, Any]] = []
        self.on_event: EventCallback | None = None
        self.on_approval_needed: ApprovalCallback | None = None
        self.on_ask_user_needed: Callable[[dict], None] | None = None

    def request_cancel(self) -> None:
        self._cancel.set()
        try:
            from tokbee.core.subprocess_util import kill_all_cancellable_runs

            kill_all_cancellable_runs(cancel_event=self._cancel)
        except Exception:
            pass
        # 若卡在审批/澄清，解开等待
        self.resolve_approval([{"type": "reject", "message": "用户取消运行"}])
        self.resolve_ask_user({"cancelled": True})

    def _mark_experience_updated(self) -> None:
        """工具成功写入经验后标记，结束兜底据此跳过自动总结。"""
        self._experience_updated_by_tool = True

    @staticmethod
    def _graph_recursion_limit(max_steps: int) -> int:
        """Deep Agents usually consume one graph tick for model and one for tools."""
        return max(4, max(1, int(max_steps)) * 2 + 1)

    def _configure_step_budget(self, req: RunRequest) -> None:
        self._step_budget = _StepBudget(req.max_steps)
        self._seen_tool_update_ids = set()
        self._phase_states = []

    def _graph_config(self, thread_id: str, req: RunRequest) -> dict:
        # recursion_limit is the graph-level hard stop; _StepBudget is the
        # user-facing logical limit that also counts tool updates.
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": self._graph_recursion_limit(req.max_steps),
        }

    def _consume_tool_updates(self, messages: list[Any]) -> None:
        if self._step_budget is None:
            return
        new_count = 0
        for msg in messages or []:
            tool_calls = _tool_calls_of(msg)
            if tool_calls:
                for index, call in enumerate(tool_calls):
                    key = _tool_call_id(call) or f"{_msg_fingerprint(msg)}:{index}"
                    if key not in self._seen_tool_update_ids:
                        self._seen_tool_update_ids.add(key)
                        new_count += 1
                continue
            cls = msg.__class__.__name__ if not isinstance(msg, dict) else str(msg.get("type") or "")
            role = getattr(msg, "type", None) or (
                msg.get("role") if isinstance(msg, dict) else ""
            ) or cls
            if "Tool" in cls or role in ("tool", "ToolMessage"):
                tool_call_id = getattr(msg, "tool_call_id", None)
                if not tool_call_id and isinstance(msg, dict):
                    tool_call_id = msg.get("tool_call_id")
                key = str(tool_call_id or _msg_fingerprint(msg))
                if key not in self._seen_tool_update_ids:
                    self._seen_tool_update_ids.add(key)
                    new_count += 1
        if new_count:
            self._step_budget.consume("tool_update", new_count)

    def _record_phase_state(
        self,
        *,
        phase_index: int,
        phase_type: str,
        status: str,
        detail: str = "",
    ) -> None:
        state = {
            "step": phase_index + 1,
            "type": phase_type,
            "status": status,
            "detail": detail[:500],
        }
        self._phase_states.append(state)
        self._emit(
            "info",
            f"管线第 {phase_index + 1} 步已结束：{status}"
            + (f"（{detail[:300]}）" if detail else ""),
            {
                "pipeline_step": phase_index,
                "pipeline_type": phase_type,
                "pipeline_status": status,
                "phase_end": True,
            },
        )

    def _cancelled_result(
        self, req: RunRequest, allow_auto_lesson: bool
    ) -> RunResult:
        return RunResult(
            ok=False,
            outcome="cancelled",
            error="已取消",
        )

    def _finalize_early_failure(
        self, req: RunRequest, result: RunResult, agent: Any, config: dict
    ) -> None:
        """Do not skip experience repair when a pipeline AI turn exits early."""
        if result.outcome not in ("failed", "incomplete"):
            return
        err = result.error or f"AI 阶段未完成（{result.outcome}）"
        self._emit("error", f"AI 阶段未完成：{err}", {"outcome": result.outcome})
        path = ""
        try:
            state = agent.get_state(config)
            values = getattr(state, "values", None) or {}
            messages = values.get("messages") if isinstance(values, dict) else None
            if messages:
                path = build_success_path_from_messages(list(messages))
        except Exception:
            pass
        lesson = self._ensure_lesson_written(
            req,
            "partial" if result.outcome == "incomplete" else "failed",
            "AI 阶段未完成",
            err,
            success_path=path,
        )
        if lesson and not result.lesson_id:
            result.lesson_id = lesson.id

    def resolve_approval(self, decisions: list[dict]) -> None:
        self._approval_decisions = decisions
        self._approval_event.set()

    def resolve_ask_user(self, answers: dict) -> None:
        self._ask_user_answers = answers if isinstance(answers, dict) else {"cancelled": True}
        self._ask_user_event.set()

    def _emit_stream_delta(self, target: str, delta: str) -> None:
        """流式增量事件（agent_stream）：只驱动 UI 实时气泡，不落盘、不入本轮轨迹。

        与 `_emit` 不同：`_emit` 会写事件缓冲（供对话/经验总结），token 级
        增量不能混进去，否则总结会读到半截文本；也不进 store，避免 events.jsonl 爆炸。
        """
        self._event_log.emit_stream(self.on_event, target, delta)

    def _emit(self, kind: str, content: str, meta: dict | None = None) -> None:
        self._event_log.emit(self.on_event, kind, content, meta)

    def _snapshot_run_events(self) -> list:
        """线程安全地取当前事件快照。"""
        return self._event_log.snapshot()

    def _wait_approval(self, pending: list[dict]) -> list[dict]:
        self._approval_event.clear()
        self._approval_decisions = None
        if self.on_approval_needed:
            self.on_approval_needed(pending)
        # 总超时兜底（真实 deadline，避免窗口关闭/审批无响应时线程永久阻塞）；默认 1 小时
        deadline = time.monotonic() + (
            float(getattr(self, "_approval_wait_timeout", 3600.0) or 3600.0)
        )
        cancelled = False
        while not self._approval_event.wait(timeout=0.5):
            if self._cancel.is_set():
                cancelled = True
                break
            if time.monotonic() >= deadline:
                logger.warning("审批等待超时，自动拒绝")
                cancelled = True
                break
        if cancelled and self._cancel.is_set():
            return [{"type": "reject", "message": "用户取消"} for _ in pending]
        decisions = self._approval_decisions or [
            {"type": "reject", "message": "无审批结果"} for _ in pending
        ]
        # 数量对齐
        if len(decisions) < len(pending):
            decisions = list(decisions) + [
                {"type": "reject", "message": "未提供决策"}
                for _ in range(len(pending) - len(decisions))
            ]
        return decisions[: len(pending) or 1]

    def _wait_ask_user(self, payload: dict) -> dict:
        self._ask_user_event.clear()
        self._ask_user_answers = None
        if self.on_ask_user_needed:
            self.on_ask_user_needed(payload)
        deadline = time.monotonic() + (
            float(getattr(self, "_ask_user_wait_timeout", 3600.0) or 3600.0)
        )
        while not self._ask_user_event.wait(timeout=0.5):
            if self._cancel.is_set():
                return {"cancelled": True}
            if time.monotonic() >= deadline:
                logger.warning("等待澄清超时，按取消处理")
                return {"cancelled": True}
        return self._ask_user_answers or {"cancelled": True}


    def _with_session_context(self, user_message: Any) -> Any:
        context = (self._session_context_block or "").strip()
        if isinstance(user_message, list):
            if not context:
                return user_message
            return [{"type": "text", "text": context}, *user_message]
        return compose_user_with_context(str(user_message or ""), context)

    def _context_already_in_state(self, agent, config: dict) -> bool:
        """读 graph 已持久化消息：历史首条 user 是否已注入【会话上下文】。

        run()/run_chat() 每次都会新建 AgentRunner，本 runner 的 _context_injected
        无法跨轮生效，因此落到 checkpointer 的持久化消息上判重：只要首条 user 的消息
        以固定哨兵头开头，就说明本会话已注入过——即使换成新 runner 也成立。
        这保证第 2+ 轮的 user 只含纯问题，命中段最小化。
        """
        try:
            state = agent.get_state(config)
        except Exception:
            return False
        values = getattr(state, "values", None) or {}
        messages = values.get("messages") if isinstance(values, dict) else None
        if not messages:
            return False
        for m in messages:
            role = str(
                getattr(m, "type", None)
                or (m.get("role") if isinstance(m, dict) else "")
                or ""
            )
            if role not in ("user", "human", "HumanMessage"):
                continue
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
            if _message_text(content).startswith(_CONTEXT_SENTINEL):
                return True
        return False

    def _inject_session_context_once(
        self, payload: Any, *, agent=None, config: dict | None = None
    ) -> Any:
        """仅首条用户消息注入【会话上下文】，保持后续轮次 append-only 前缀稳定。

        去重依据二选一（满足即跳过）：
        1. 本 runner 已注入过（同轮内多阶段/续跑去重）；
        2. graph 持久化消息已含固定哨兵头（跨新 runner 去重）。
        """
        if self._context_injected or not isinstance(payload, dict):
            return payload
        msgs = payload.get("messages")
        if not isinstance(msgs, list) or not msgs:
            return payload
        if agent is not None and config is not None and self._context_already_in_state(agent, config):
            self._context_injected = True
            return payload
        out_msgs = []
        injected = False
        for m in msgs:
            if (
                not injected
                and isinstance(m, dict)
                and (m.get("role") or "") == "user"
            ):
                content = m.get("content") or ""
                out_msgs.append(
                    {**m, "content": self._with_session_context(content)}
                )
                injected = True
            else:
                out_msgs.append(m)
        if injected:
            self._context_injected = True
            return {**payload, "messages": out_msgs}
        return payload
