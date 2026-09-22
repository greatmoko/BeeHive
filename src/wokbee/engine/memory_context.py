"""Replace completed turns only in outgoing requests; retain original graph history."""
from __future__ import annotations

import hashlib
import json

from deepagents.middleware.summarization import SummarizationMiddleware
from langchain_core.messages import HumanMessage

from wokbee.engine.memory_runtime import message_text, parse_json
from sysprompt import MEMORY_SUMMARY_SYSTEM_PROMPT


def memory_token_count(messages, *, tools=None):
    from tokbee.core.context_manager import estimate_content_tokens, estimate_text_tokens
    from langchain_core.utils.function_calling import convert_to_openai_tool
    total = 0
    for message in messages:
        total += estimate_content_tokens(getattr(message, "content", "")) + 4
        calls = getattr(message, "tool_calls", None)
        if calls:
            total += estimate_text_tokens(json.dumps(calls, ensure_ascii=False))
    for tool in tools or []:
        total += estimate_text_tokens(json.dumps(convert_to_openai_tool(tool), ensure_ascii=False))
    return total


def turn_groups(messages):
    groups = []
    for msg in messages:
        if getattr(msg, "type", "") == "human":
            key = (getattr(msg, "additional_kwargs", {}) or {}).get("memory_turn_id")
            if not key:
                key = "legacy-" + hashlib.sha256(str(getattr(msg, "id", None) or msg.content).encode()).hexdigest()[:32]
            if not groups or groups[-1][0] != key:
                groups.append((key, []))
        if not groups:
            groups.append(("prefix", []))
        groups[-1][1].append(msg)
    return groups


class SessionMemoryMiddleware(SummarizationMiddleware):
    # Same name replaces the framework's default middleware in its original position.
    @property
    def name(self):
        return "SummarizationMiddleware"

    def __init__(self, model, *, backend, session, store, context_window, emit):
        self.session = session
        self.store = store
        self.window = max(1024, int(context_window or 32768))
        self.trigger, self.target = store.thresholds()
        self.emit = emit
        self.summary_model = model
        self.replaced = set()
        super().__init__(model, backend=backend, trigger=("tokens", int(self.window * self.target)),
                         keep=("messages", 4), token_counter=memory_token_count)

    def _get_effective_messages(self, request):
        # Old compaction events refer to different offsets; never apply them to this projection.
        return list(request.messages)

    def _project(self, request):
        original = list(request.messages)
        snapshot = next(((getattr(m, "additional_kwargs", {}) or {}).get("memory_global")
                         for m in original if (getattr(m, "additional_kwargs", {}) or {}).get("memory_global")),
                        self.store.global_text(self.store.global_memory()))
        global_message = HumanMessage(content="【全局记忆（用户资料，不覆盖安全与权限规则）】\n" + snapshot,
                                      id="global-memory-snapshot")
        self._global_message = global_message
        groups = turn_groups(original)
        records = {r["turn_id"]: r["text"] for r in self.session.records()}

        def rendered():
            result = [global_message]
            for key, messages in groups:
                if key in self.replaced and key in records:
                    result.append(HumanMessage(content=f"【会话记忆 {key}】\n{records[key]}"))
                else:
                    result.extend(messages)
            return result

        def count(messages):
            return self._count_tokens(messages, request.system_message, request.tools)

        messages = rendered()
        active = count([global_message, *original]) >= self.window * self.trigger
        if active:
            # The last group is the current, unfinished turn, including all its tool exchanges.
            for key, old_messages in groups[:-1]:
                if key == "prefix" or key in self.replaced:
                    continue
                if key not in records:
                    # Backfill complete pre-upgrade turns only when compression needs them.
                    try:
                        response = self.summary_model.invoke([
                            {"role": "system", "content": MEMORY_SUMMARY_SYSTEM_PROMPT},
                            {"role": "user", "content": "\n".join(message_text(m) for m in old_messages)},
                        ])
                        summary = parse_json(message_text(response))
                        record = self.session.append(key, goal=summary["goal"], result=summary["result"],
                                                     unresolved=summary.get("unresolved", "无"), keywords=summary.get("keywords", ""))
                        records[key] = record["text"]
                    except Exception as exc:
                        self.emit("error", f"历史会话记忆补写失败，保留原文：{exc}")
                        continue
                self.replaced.add(key)
                messages = rendered()
                if count(messages) < self.window * self.target:
                    break
        used = count(messages)
        return request.override(messages=messages), active and used >= self.window * self.target

    def _with_global(self, request):
        if not any(getattr(m, "id", "") == "global-memory-snapshot" for m in request.messages):
            request = request.override(messages=[self._global_message, *request.messages])
        used = self._count_tokens(request.messages, request.system_message, request.tools)
        self.emit("context_usage", "上下文用量", {"used": used, "limit": self.window})
        return request

    def wrap_model_call(self, request, handler):
        projected, fallback = self._project(request)
        def deliver(current):
            return handler(self._with_global(current))
        if not fallback:
            return deliver(projected)
        # Framework compression works on the projected request. Its cutoff is NOT an offset
        # in the original graph state, so do not commit its projection-specific state command.
        response = super().wrap_model_call(projected, deliver)
        return getattr(response, "model_response", response)

    async def awrap_model_call(self, request, handler):
        import asyncio
        projected, fallback = await asyncio.to_thread(self._project, request)
        async def deliver(current):
            return await handler(self._with_global(current))
        if not fallback:
            return await deliver(projected)
        response = await super().awrap_model_call(projected, deliver)
        return getattr(response, "model_response", response)
