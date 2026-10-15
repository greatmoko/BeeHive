"""memory/events.jsonl 轮次事件：写入 schema、截断与轮次上下文。

schema（每行一个 JSON 对象，按行追加；轮次头字段只写一次）：
    round            轮次序号（从 1 起，全项目递增；每行都有）
    round_id         轮次 ID（短 ID，如 r1/r2，仅该轮首事件写入）
    round_started_at 轮次开始时间（仅该轮首事件写入）
    round_type       轮次类型：run（运行）| chat（聊天）| design（设计）（仅首事件写入）
    user_input       本轮用户输入（交互模式 chat/design，仅首事件写入）
    goal             本轮项目目标（运行模式 run，仅首事件写入）
    seq              事件在本轮内的序号（从 1 递增；排在 id 前，每行都有）
    id               事件 ID
    time             事件时间
    type             事件类型：user_input | ai_reply | tool_call | tool_result
                     | approval | error | system_notice
    tool_name        工具名（工具事件，为空不写）
    call_id          工具调用 ID（工具事件，为空不写）
    content          事件正文
    truncated        正文是否被截断
    original_chars   截断前原始字符数
    meta             可选扩展字段（session_end / phase / script 等，仅非空时写入）

精简规则：不损失信息的前提下避免重复——轮次头字段（round_id/round_started_at/
round_type/user_input/goal）只在每轮第一条事件（seq=1）落盘，后续事件只有
round+seq，读取时按 round 分组即可还原完整上下文；运行模式只记项目目标、
交互模式只记用户对话。系统事件（项目创建、归档通知等）round=0 且无轮次头。

截断规则：content > 4000 字符时只持久化前 2000 + 后 2000，中间加省略标记
（标记不计入 4000）；truncated=true、original_chars=原始长度。
只截断持久化内容，不影响 Agent 上下文中收到的完整输出。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from beehive.features.wokbee.projects.models import ProjectEvent, _now

# 持久化截断上限（字符数）
EVENT_CONTENT_MAX_CHARS = 4_000
_KEEP_HEAD = 2_000
_KEEP_TAIL = 2_000

ROUND_TYPES = ("run", "chat", "design")
EVENT_TYPES = (
    "user_input", "ai_reply", "tool_call", "tool_result",
    "approval", "error", "system_notice",
)

# 旧 kind → 新 type
_KIND_TO_TYPE = {
    "user": "user_input",
    "agent": "ai_reply",
    "approval": "approval",
    "error": "error",
}
# 新 type → 旧 kind（读取兼容 UI/经验链路）
_TYPE_TO_KIND = {
    "user_input": "user",
    "ai_reply": "agent",
    "tool_call": "tool",
    "tool_result": "tool",
    "approval": "approval",
    "error": "error",
    "system_notice": "info",
}

# 进程内轮次上下文：project_id -> dict(round, round_id, round_started_at, round_type, user_input, goal, seq)
_round_state: dict[str, dict] = {}
_round_lock = threading.RLock()


def truncate_log_content(content: str) -> tuple[str, bool, int]:
    """返回 (持久化正文, 是否截断, 原始字符数)。

    > 4000 字符：保留前 2000 + 后 2000，中间省略标记不计入上限。
    """
    original = len(content)
    if original <= EVENT_CONTENT_MAX_CHARS:
        return content, False, original
    omitted = original - _KEEP_HEAD - _KEEP_TAIL
    marker = f"\n…（中间省略 {omitted} 字符）\n"
    return content[:_KEEP_HEAD] + marker + content[-_KEEP_TAIL:], True, original


# ---------------------------------------------------------------- 轮次上下文

def _read_last_payload(events_file: Path) -> dict | None:
    """读事件文件最后一行（不解析整个文件）。"""
    try:
        if not events_file.exists():
            return None
        last: str | None = None
        with events_file.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        if not last:
            return None
        return json.loads(last)
    except (OSError, json.JSONDecodeError):
        return None


def next_round_no(events_file: Path) -> int:
    """下一轮序号 = 文件内最大轮号 + 1（无文件/坏行时为 1）。

    系统事件（项目创建、归档通知等，round=0）不占轮号。
    """
    max_round = 0
    try:
        if not events_file.exists():
            return 1
        with events_file.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict) and isinstance(data.get("round"), int) and data["round"] >= 1:
                    max_round = max(max_round, int(data["round"]))
    except OSError:
        return 1
    return max_round + 1


def _recover_round(project_id: str, events_file: Path) -> dict | None:
    """进程重启后从文件尾部恢复未结束的轮次上下文。

    轮次头字段只在该轮首事件（seq=1）行上：恢复时用该轮第一条行还原头信息，
    用该轮行数还原 seq。
    """
    rows: list[dict] = []
    try:
        if not events_file.exists():
            return None
        with events_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict) and isinstance(data.get("round"), int) and data["round"] >= 1:
                    rows.append(data)
    except OSError:
        return None
    if not rows:
        return None
    last = rows[-1]
    rno = int(last.get("round") or 1)
    group = [r for r in rows if int(r.get("round") or 0) == rno]
    head = group[0]
    return {
        "round": rno,
        "round_id": str(head.get("round_id") or f"r{rno}"),
        "round_started_at": str(head.get("round_started_at") or ""),
        "round_type": str(head.get("round_type") or "run"),
        "user_input": str(head.get("user_input") or ""),
        "goal": str(head.get("goal") or ""),
        "seq": len(group),
    }


def begin_round(
    project_id: str,
    *,
    round_id: str = "",
    round_type: str = "run",
    user_input: str = "",
    goal: str = "",
    events_file: Path | None = None,
    reuse: bool = False,
) -> dict:
    """开始（或续用）一轮。等待审批后恢复传入 reuse=True 仍属同一轮。

    round_id 为空时自动生成短 ID（r+轮号）。进程重启后的恢复（reuse=True
    且进程内无状态）从事件文件还原轮次，不新开轮号。
    """
    round_type = round_type if round_type in ROUND_TYPES else "run"
    with _round_lock:
        existing = _round_state.get(project_id)
        if existing and (reuse or (round_id and existing["round_id"] == round_id)):
            if round_type in ROUND_TYPES and round_type != "run":
                existing["round_type"] = round_type
            if user_input:
                existing["user_input"] = str(user_input)
            return existing
        if reuse and existing is None and events_file is not None:
            recovered = _recover_round(project_id, events_file)
            if recovered is not None:
                if round_type in ROUND_TYPES and round_type != "run":
                    recovered["round_type"] = round_type
                if user_input:
                    recovered["user_input"] = str(user_input)
                _round_state[project_id] = recovered
                return recovered
        no = existing["round"] + 1 if existing else next_round_no(events_file or Path("nonexistent"))
        ctx = {
            "round": no,
            "round_id": str(round_id) if round_id else f"r{no}",
            "round_started_at": _now(),
            "round_type": round_type,
            "user_input": str(user_input or ""),
            "goal": str(goal or ""),
            "seq": 0,
        }
        _round_state[project_id] = ctx
        return ctx


def set_round_start(project_id: str, started_at: str) -> None:
    with _round_lock:
        ctx = _round_state.get(project_id)
        if ctx is not None and not ctx.get("round_started_at"):
            ctx["round_started_at"] = str(started_at)


def current_round(project_id: str, events_file: Path | None = None) -> dict | None:
    """当前轮次上下文；进程内没有时尝试从文件尾部恢复。"""
    with _round_lock:
        ctx = _round_state.get(project_id)
        if ctx is not None:
            return ctx
    recovered = _recover_round(project_id, events_file) if events_file else None
    if recovered is not None:
        with _round_lock:
            _round_state.setdefault(project_id, recovered)
            return _round_state[project_id]
    return None


def end_round(project_id: str) -> None:
    with _round_lock:
        _round_state.pop(project_id, None)


# ---------------------------------------------------------------- 写入

def event_type_for(kind: str, meta: dict) -> str:
    if kind == "tool":
        return "tool_call" if (meta or {}).get("phase") == "call" else "tool_result"
    return _KIND_TO_TYPE.get(kind, "system_notice")


def build_event_payload(project_id: str, event: ProjectEvent, events_file: Path) -> dict:
    """把 ProjectEvent 转成 events.jsonl 行字典（自动带轮次上下文与 seq）。"""
    meta = event.meta if isinstance(event.meta, dict) else {}
    ctx = current_round(project_id, events_file) or {
        # 不属于任何会话轮次的系统事件（项目创建、归档通知等）：
        # round=0、round_id 为空，不占用轮次序号。
        "round": 0,
        "round_id": "",
        "round_started_at": "",
        "round_type": "run",
        "user_input": "",
        "goal": "",
        "seq": 0,
    }
    with _round_lock:
        ctx["seq"] = int(ctx.get("seq") or 0) + 1
        seq = ctx["seq"]
    content, truncated, original_chars = truncate_log_content(str(event.content or ""))
    etype = event_type_for(event.kind, meta)
    # 精简：轮次头字段只在该轮首事件（seq=1）落盘；运行模式记 goal、
    # 交互模式记 user_input；系统事件（round=0）无轮次头。
    payload: dict[str, Any] = {"round": ctx.get("round", 1)}
    if seq == 1 and ctx.get("round_id"):
        payload["round_id"] = ctx.get("round_id", "")
        payload["round_started_at"] = ctx.get("round_started_at", "")
        payload["round_type"] = ctx.get("round_type", "run")
        if ctx.get("user_input"):
            payload["user_input"] = ctx["user_input"]
        if ctx.get("goal"):
            payload["goal"] = ctx["goal"]
    payload.update({
        "seq": seq,
        "id": event.id,
        "time": event.created_at,
        "type": etype,
    })
    tool_name = str(meta.get("tool") or meta.get("tool_name") or "")
    call_id = str(meta.get("tool_call_id") or meta.get("call_id") or "")
    if tool_name:
        payload["tool_name"] = tool_name
    if call_id:
        payload["call_id"] = call_id
    payload.update({
        "content": content,
        "truncated": truncated,
        "original_chars": original_chars,
    })
    if meta:
        payload["meta"] = meta
    return payload


# ---------------------------------------------------------------- 读取

def parse_event_payload(data: dict) -> ProjectEvent:
    """events.jsonl 行 → ProjectEvent（兼容旧版 kind/content/created_at schema）。"""
    if "kind" in data:
        return ProjectEvent.from_dict(data)
    etype = str(data.get("type") or "system_notice")
    kind = _TYPE_TO_KIND.get(etype, "info")
    meta = dict(data.get("meta") or {})
    if data.get("tool_name"):
        meta.setdefault("tool", data["tool_name"])
    if data.get("call_id"):
        meta.setdefault("tool_call_id", data["call_id"])
    if etype == "tool_call":
        meta.setdefault("phase", "call")
    elif etype == "tool_result":
        meta.setdefault("phase", "callback")
    for key in ("round", "round_type", "seq", "round_started_at", "user_input", "goal"):
        if data.get(key) is not None:
            meta.setdefault(key, data[key])
    if data.get("round_id"):
        meta.setdefault("round_id", data["round_id"])
    event = ProjectEvent(
        id=str(data.get("id") or ""),
        kind=kind,
        content=str(data.get("content") or ""),
        created_at=str(data.get("time") or ""),
        meta=meta,
    )
    return event


def load_events_file(path: Path) -> list[ProjectEvent]:
    """整读 events.jsonl → ProjectEvent 列表；坏行跳过。"""
    events: list[ProjectEvent] = []
    try:
        if not path.exists():
            return events
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(parse_event_payload(json.loads(line)))
                except (json.JSONDecodeError, TypeError, KeyError):
                    continue
    except OSError:
        return events
    return events


__all__ = [
    "EVENT_CONTENT_MAX_CHARS",
    "ROUND_TYPES",
    "EVENT_TYPES",
    "truncate_log_content",
    "begin_round",
    "set_round_start",
    "current_round",
    "end_round",
    "next_round_no",
    "event_type_for",
    "build_event_payload",
    "parse_event_payload",
    "load_events_file",
]
