"""聊天/设计会话协调与近期时间线读取。"""
from __future__ import annotations
import logging
import json
from pathlib import Path
from beehive.features.wokbee.projects.models import _now
from beehive.features.wokbee.experience.lesson_events import slice_latest_round
from beehive.features.wokbee.execution.runner_models import RunRequest, RunResult, StepLimitExceeded
from beehive.system.agent.message_content import _attachment_content
from beehive.system.agent.message_content import _extract_text
from beehive.features.wokbee.execution.runner_support import _format_engine_error
from beehive.features.wokbee.execution.runner_modes import ModePolicy, mode_label

logger = logging.getLogger("wokbee")


def _run_conversation(self, req: RunRequest, policy: ModePolicy) -> RunResult:
    """对话共用流程；模式差异由策略提供。"""
    self._event_log.reset()
    self._context_injected = False
    # 保留设计模式已有的 thread_id，升级后继续使用原 checkpoint。
    thread_id = f"wokbee-chat-{req.project.id}"
    chat_thread_id = str(getattr(req, "chat_thread_id", "") or "").strip()
    if chat_thread_id:
        thread_id += f"-{chat_thread_id}"
    self._configure_step_budget(req)
    config = self._graph_config(thread_id, req)
    seen_msg_ids: set[str] = set()

    question = (req.user_message or "").strip()
    if not question and not req.attachments:
        return RunResult(ok=False, outcome="failed", error="提问内容为空")

    try:
        agent = self.build_agent(req, mode=policy.name)
    except Exception as e:
        logger.exception("创建交互 Agent 失败")
        return RunResult(ok=False, outcome="failed", error=str(e))

    self._emit(
        "agent",
        policy.intro
        + f"模型：{req.resolved.provider_name}/{req.resolved.model_id}\n"
        + policy.capabilities,
        {"phase": "hint"},
    )

    # 仅空 checkpoint 用时间线补充背景；后续轮次沿用历史消息（含压缩摘要）。
    values = {}
    try:
        state = agent.get_state(config)
        values = getattr(state, "values", None) or {}
        has_history = bool(values.get("messages")) if isinstance(values, dict) else False
    except Exception:
        logger.exception("读取对话 checkpoint 失败，跳过时间线摘录")
        has_history = True
    self._context_injected = has_history
    recent = (
        self._recent_events_digest(req.project_root, limit=40)
        if policy.timeline and not has_history else ""
    )
    metadata, update = {}, ""
    if policy.design_workspace and getattr(req, "design_context", ""):
        prepare = self.design_question_preparer
        if prepare is None:
            raise RuntimeError("设计运行缺少需求上下文适配，请通过应用运行器工厂创建")
        question, metadata, update = prepare(
            question, req.design_context, values, has_history
        )
    if update:
        self._emit("user", update)
    sent_note = f"（发送时间：{_now()}）"
    user_content = f"【当前模式】{mode_label(policy.name)}\n\n{question}\n\n{sent_note}"
    if recent:
        user_content = (
            f"{user_content}\n\n"
            "——\n【近期时间线摘录（供参考，回答不必复述全文）】\n"
            f"{recent}"
        )
    user_content = _attachment_content(user_content, req.attachments)
    user_message = {"role": "user", "content": user_content}
    if metadata:
        # 元数据不进入模型文本；每轮保留版本，跟随 checkpoint 的提交与生命周期。
        user_message["additional_kwargs"] = metadata

    try:
        early = self._run_agent_turn(
            agent,
            config,
            seen_msg_ids,
            req,
            payload={"messages": [user_message]},
            first=True,
            allow_auto_lesson=False,
            start_hint="Agent 处理中…",
        )
        if early:
            # 对话模式：取消/失败原样返回；审批等待也返回
            return early
        if self._cancel.is_set():
            return self._cancelled_result(req, False)

        final_text = ""
        try:
            state = agent.get_state(config)
            values = getattr(state, "values", None) or {}
            messages = values.get("messages") if isinstance(values, dict) else None
            if messages:
                # 只取最终文本；流式阶段已写入时间线，禁止把 checkpoint 全量历史再刷一遍
                final_text = _extract_text(list(messages))
        except Exception:
            pass

        self._emit("info", "本轮回复已完成")
        return RunResult(ok=True, outcome="success", final_text=final_text)
    except StepLimitExceeded as e:
        err = str(e)
        self._emit("error", err, {"outcome": "incomplete", "max_steps": e.limit})
        return RunResult(ok=False, outcome="incomplete", error=err)
    except Exception as e:
        logger.exception("交互失败")
        err = _format_engine_error(e)
        self._emit("error", f"交互失败：{err}")
        return RunResult(ok=False, outcome="failed", error=err)

def _recent_events_digest(project_root: Path, *, limit: int = 40) -> str:
    try:
        from beehive.system.memory.session import SessionMemory
        memory = SessionMemory(project_root)
        if memory.records():
            return memory.read(recent=1)
        from beehive.features.wokbee.projects.context_usage import (
            events_as_messages,
            load_context_state,
        )
        from beehive.features.wokbee.projects.round_events import load_events_file
        from beehive.system.memory import context as ctxman

        events = load_events_file(events_path(project_root))
        # 只取最新一轮，避免旧轮日志灌进上下文
        events = slice_latest_round(events)
        messages = events_as_messages(events)
        state = load_context_state(project_root)
        summary, active, _ = ctxman.slice_after_compaction(
            messages, state.get("compaction_points") or [],
        )
        rows: list[str] = []
        if summary:
            body = summary.strip().replace("\n", " ")
            if len(body) > 400:
                body = body[:400] + "…"
            rows.append(f"- [summary] {body}")
        for msg in active:
            kind = msg.get("kind") or msg.get("role") or "?"
            body = (msg.get("content") or "").strip().replace("\n", " ")
            if not body:
                continue
            if len(body) > 220:
                body = body[:220] + "…"
            rows.append(f"- [{kind}] {body}")
        if not rows:
            return ""
        return "\n".join(rows[-limit:])
    except Exception:
        logger.exception("读取近期时间线失败")
        return ""
