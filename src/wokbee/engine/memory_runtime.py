"""Shared memory tools and end-of-turn processing for run/chat/design."""
from __future__ import annotations

import json
import logging
import uuid
from functools import wraps

from wokbee.core.memory import MEMORY_RULES, MemoryStore, SessionMemory

log = logging.getLogger("wokbee")


def build_memory_tools(session, store, read_records, *, memory_state=None):
    from langchain_core.tools import tool
    batch_completed = False
    replacement_count = 0
    memory_state = memory_state if memory_state is not None else {}
    memory_state.setdefault("atomic", [])
    memory_state.setdefault("global", [])

    @tool
    def read_session_memory(recent: int = 5, rounds: list[str] | None = None,
                            keyword: str = "", start_line: int | None = None,
                            end_line: int | None = None) -> str:
        """按最近N轮、指定轮次编号/ID、关键词或1起始行范围读取当前项目/需求的MD记忆。"""
        return session.read(recent, rounds, keyword, start_line, end_line)

    @tool
    def search_atomic_memory(keywords: list[str], limit: int = 50) -> str:
        """跨项目关键词搜索最新原子记忆，只返回ID、关键词、类型，不增加调取次数。必要时最多二次检索。"""
        candidates = store.search(keywords, limit)
        return json.dumps(candidates, ensure_ascii=False)

    @tool
    def read_atomic_memory(ids: list[str]) -> str:
        """按候选ID批量读取有用的记忆正文，每个实际读取的ID调取次数增加一次。"""
        records = store.read(ids)
        read_records.update({r["id"]: r for r in records})
        return json.dumps(records, ensure_ascii=False)

    @tool
    def write_atomic_memory(
        memories: list[dict] | None = None,
        keywords: list[str] | None = None,
        kind: str = "",
        body: str = "",
        file_url: str = "",
        previous_id: str | None = None,
    ) -> str:
        """一次批量保存独立记忆；兼容旧版单条参数，修订使用 previous_id。"""
        nonlocal batch_completed
        if batch_completed:
            return json.dumps({"status": "already_completed"}, ensure_ascii=False)
        if memories is None:
            if keywords is None:
                return json.dumps({"status": "error", "message": "需要memories或单条记忆参数"}, ensure_ascii=False)
            memories = [{
                "keywords": keywords,
                "kind": kind,
                "body": body,
                "file_url": file_url,
                "previous_id": previous_id,
            }]
        elif not isinstance(memories, list):
            return json.dumps({
                "status": "error",
                "message": "memories必须是列表",
            }, ensure_ascii=False)
        results = []
        kind_aliases = {"event": "事件", "fact": "事实", "rule": "规则", "preference": "偏好"}
        for item in memories[:20]:
            if not isinstance(item, dict):
                results.append({"status": "error", "message": "记忆条目必须是对象"})
                continue
            try:
                item_keywords = item.get("keywords") or []
                item_kind = str(item.get("kind") or item.get("type") or "").strip()
                item_kind = kind_aliases.get(item_kind.casefold(), item_kind)
                item_body = item.get("body") or ""
                item_file_url = item.get("file_url") or ""
                item_previous_id = item.get("previous_id")
                candidates = store.search(item_keywords)
                ident = store.write(
                    item_keywords, item_kind, item_body,
                    item_file_url, item_previous_id,
                )
                results.append({
                    "status": "saved", "id": ident,
                    "candidate_count": len(candidates),
                })
            except Exception as exc:
                results.append({"status": "error", "message": str(exc)})
        saved = [
            (item, result)
            for item, result in zip(memories[:20], results)
            if result.get("status") == "saved"
        ]
        for item, result in saved:
            action = "修订" if item.get("previous_id") else "新增"
            keywords = "、".join(str(k) for k in item.get("keywords") or [])
            body = " ".join(str(item.get("body") or "").split())[:180]
            memory_state["atomic"].append(f"- {action} #{result['id']} [{item.get('kind') or item.get('type') or '记忆'}] {keywords}：{body}")
        if saved:
            batch_completed = True
        else:
            messages = "; ".join(str(result.get("message") or "参数无效") for result in results)
            return json.dumps({"status": "error", "saved": 0, "results": results, "message": messages}, ensure_ascii=False)
        return json.dumps({
            "status": "completed",
            "saved": len(saved),
            "results": results,
            "message": "",
        }, ensure_ascii=False)

    @tool
    def propose_global_memory(module: str, operation: str, new: str, reason: str, target: str = "") -> str:
        """提出一条全局记忆建议。module 只能是用户画像、环境信息、全局规则、记忆使用规则；operation 只能是 add 或 replace。new 和 reason 必须有内容。add 不传 target；replace 的 target 必须是当前规则的完整原文，整轮最多替换两条。需用户确认后才生效。"""
        nonlocal replacement_count
        if operation == "replace":
            if replacement_count >= 2:
                return json.dumps({"status": "error", "message": "本轮最多替换两条全局记忆规则"}, ensure_ascii=False)
        try:
            ident = store.propose_rule(module, operation, new, reason, target)
        except ValueError as exc:
            return json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False)
        if operation == "replace":
            replacement_count += 1
        action = "新增" if operation == "add" else "替换"
        detail = f"- {action}建议（待确认）模块：{module}\n"
        if target:
            detail += f"  目标规则：{target[:240]}\n"
        detail += f"  建议规则：{new[:240]}\n  原因：{reason[:160]}"
        memory_state["global"].append(detail)
        return json.dumps({"status": "proposed", "id": ident}, ensure_ascii=False)

    return [read_session_memory, search_atomic_memory, read_atomic_memory, write_atomic_memory, propose_global_memory]


def parse_json(text):
    text = (text or "").strip()
    if not text:
        raise ValueError("模型未返回记忆整理结果")
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # Models sometimes add a short lead-in despite the strict JSON request.
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型未返回有效JSON")
        try:
            result = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("模型返回的记忆JSON格式无效") from exc
    if not isinstance(result, dict):
        raise ValueError("记忆整理结果必须是JSON对象")
    return result


def message_text(message):
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return str(content or "")


def memory_turn(fn):
    """One finalizer per public operation, even when run delegates to chat/design."""
    @wraps(fn)
    def wrapped(self, req, *args, **kwargs):
        if getattr(self, "_memory_active", False):
            return fn(self, req, *args, **kwargs)
        self._memory_active = True
        self._memory_turn_id = uuid.uuid4().hex
        self._memory_reads = {}
        self._memory_notifications = {"atomic": [], "global": []}
        self._memory_model = None
        result = None
        try:
            result = fn(self, req, *args, **kwargs)
            return result
        finally:
            try:
                # An unresolved approval is not a completed turn.
                if result is not None and result.outcome != "awaiting_approval":
                    finalize_memory(self, req, result)
            except Exception as exc:
                log.exception("会话记忆收尾失败")
                self._emit("error", f"记忆整理失败，原始聊天保留：{exc}")
            finally:
                self._memory_active = False
                self._emit("info", "— 本轮运行/对话结束 —", {"session_end": True})
    return wrapped


def finalize_memory(runner, req, result):
    from wokbee.core.credential_store import redact_text

    session = SessionMemory(req.project_root)
    goal = redact_text(req.user_message or req.project.goal or "")
    final = redact_text(result.final_text or result.error or result.outcome)
    fallback = dict(goal=goal, result=final, unresolved="无" if result.ok else result.error or result.outcome,
                    keywords="", timestamp=None)
    try:
        session.append(runner._memory_turn_id, **fallback)
        runner._emit("info", "【会话记忆】已追加本轮记录", {"memory_kind": "session", "memory_turn_id": runner._memory_turn_id})
        state = getattr(runner, "_memory_notifications", {})
        atomic = state.get("atomic") or []
        runner._emit("info", "【原子记忆】\n" + ("\n".join(atomic) if atomic else "本轮未新增或修订原子记忆。"),
                     {"memory_kind": "atomic"})
        global_updates = state.get("global") or []
        runner._emit("info", "【全局记忆】\n" + ("\n".join(global_updates) if global_updates else "本轮未提交全局记忆更新建议。"),
                     {"memory_kind": "global"})
    except Exception as exc:
        session.append(runner._memory_turn_id, **fallback)
        runner._emit("error", f"会话记忆写入失败：{exc}")
