"""经验轨迹摘要组装：时间顺序、连续重复和关键线索。"""
from __future__ import annotations
from beehive.features.wokbee.experience.lesson_event_details import _collapse_ws, _clip, _parse_tool_call_content, _parse_tool_callback_content, _args_digest, _result_digest, _collect_success_hints, _fingerprint_line, _AGENT_DIGEST_CHARS, _ERROR_DIGEST_CHARS, _SUCCESS_SECTION_MAX

def build_lesson_digest(events: list | None, *, max_chars: int = 50000) -> str:
    """结构化压缩运行轨迹，供经验总结 AI 使用。

    - 工具 call/result 压成短行；agent 截短；error 尽量保留
    - 连续相同 tool+参数+结果去重为 ×N
    - 时间序「压缩轨迹」在前（消息以序数 1. 2. 3. … 编号）；无序「关键线索」在后，避免干扰 success_path 顺序
    """
    hint_ordered: list[str] = []
    hint_seen: set[str] = set()
    caution_ordered: list[str] = []
    caution_seen: set[str] = set()
    traj: list[str] = []

    last_fp = ""
    last_idx = -1
    repeat = 0

    def _flush_repeat() -> None:
        nonlocal repeat, last_idx
        if repeat > 0 and 0 <= last_idx < len(traj):
            traj[last_idx] = traj[last_idx] + f"  ×{repeat + 1}"
        repeat = 0

    def _append_traj(line: str, fp: str) -> None:
        nonlocal last_fp, last_idx, repeat
        if fp and fp == last_fp and last_idx >= 0:
            repeat += 1
            return
        _flush_repeat()
        traj.append(line)
        last_fp = fp
        last_idx = len(traj) - 1

    def _add_hint(hints: list[str]) -> None:
        for h in hints:
            key = h.strip()
            if not key or key in hint_seen:
                continue
            hint_seen.add(key)
            hint_ordered.append(key)

    def _add_caution(text: str) -> None:
        key = (text or "").strip()
        if not key or key in caution_seen:
            return
        caution_seen.add(key)
        caution_ordered.append(key)

    for ev in events or []:
        kind = (getattr(ev, "kind", "") or "").strip()
        content = (getattr(ev, "content", None) or "").strip()
        meta = getattr(ev, "meta", None) or {}
        if not isinstance(meta, dict):
            meta = {}
        if not content and kind != "tool":
            continue
        if kind not in ("tool", "agent", "error", "user", "info", "approval", "lesson"):
            continue

        if kind == "tool":
            phase = str(meta.get("phase") or "").lower()
            tool = str(meta.get("tool") or "").strip()
            status = str(meta.get("status") or "").strip()
            args = meta.get("args") if isinstance(meta.get("args"), dict) else {}

            if not phase:
                if content.startswith("**call:**") or content.startswith("call:"):
                    phase = "call"
                elif content.startswith("**callback:**") or content.startswith("callback:"):
                    phase = "callback"

            if phase == "call":
                if not tool:
                    parsed_name, parsed_args = _parse_tool_call_content(content)
                    tool = parsed_name or "tool"
                    if not args:
                        args = parsed_args
                args_s = _args_digest(args if isinstance(args, dict) else {})
                if not args_s:
                    _, parsed_args = _parse_tool_call_content(content)
                    args_s = _args_digest(parsed_args)
                line = f"call {tool}" + (f" | {args_s}" if args_s else "")
                fp = _fingerprint_line("tool", tool, "call", args_s, "")
                _append_traj(line, fp)
                _add_hint(
                    _collect_success_hints(
                        tool, "call", args if isinstance(args, dict) else {}, "", status
                    )
                )
            else:
                body = content
                if not tool:
                    tool, body = _parse_tool_callback_content(content)
                    tool = tool or "tool"
                else:
                    _, body = _parse_tool_callback_content(content)
                dig = _result_digest(body, status=status)
                line = f"result {tool} | {dig}"
                fp = _fingerprint_line("tool", tool, "callback", "", dig)
                _append_traj(line, fp)
                if "【循环检测】" in body or "循环检测" in body:
                    _add_caution(f"{tool} 触发循环检测（勿再同参重试）")
                else:
                    _add_hint(
                        _collect_success_hints(
                            tool, "callback", args, body, status or "ok"
                        )
                    )
            continue

        if kind == "agent":
            phase = str(meta.get("phase") or "")
            chunk = _clip(_collapse_ws(content), _AGENT_DIGEST_CHARS)
            if not chunk:
                continue
            tag = f"agent/{phase}" if phase else "agent"
            line = f"{tag}: {chunk}"
            _append_traj(line, _fingerprint_line("agent", phase, "", chunk, ""))
            continue

        if kind == "error":
            chunk = _clip(content, _ERROR_DIGEST_CHARS)
            line = f"error: {chunk}"
            _append_traj(line, _fingerprint_line("error", "", "", chunk, ""))
            if "循环" in content:
                _add_caution(_clip(_collapse_ws(content), 120))
            continue

        # user / info / approval / lesson — 短摘
        cap = 200 if kind in ("info", "approval", "lesson") else 160
        chunk = _clip(_collapse_ws(content), cap)
        if not chunk:
            continue
        if kind == "info" and any(
            x in chunk for x in ("准备经验总结", "正在调用 AI 总结", "cache ")
        ):
            continue
        line = f"{kind}: {chunk}"
        _append_traj(line, _fingerprint_line(kind, "", "", chunk, ""))

    _flush_repeat()

    if not traj and not hint_ordered and not caution_ordered:
        return "（无运行日志）"

    traj_body = "\n".join(
        f"{i + 1}. {line}" for i, line in enumerate(traj)
    ) if traj else "（无工具/过程事件）"
    hint_sec = ""
    if hint_ordered:
        bullets = "\n".join(f"- {h}" for h in hint_ordered[:80])
        hint_sec = (
            "## 关键线索（无序，勿当执行顺序）\n"
            "以下仅为 URL/脚本/产物等检索提示；success_path 必须以时间序「压缩轨迹」为准。\n"
            + bullets
        )
        if len(hint_sec) > _SUCCESS_SECTION_MAX:
            hint_sec = hint_sec[:_SUCCESS_SECTION_MAX] + "\n…(线索截断)"

    caution_sec = ""
    if caution_ordered:
        bullets = "\n".join(f"- {h}" for h in caution_ordered[:40])
        caution_sec = "## 失败教训线索（无序）\n" + bullets

    header = (
        "（结构化压缩轨迹：按时间序排列；文末无序线索仅供检索，"
        "勿当作 success_path 的执行顺序；非原始全文。）"
    )
    parts = [header, "## 压缩轨迹\n" + traj_body]
    if hint_sec:
        parts.append(hint_sec)
    if caution_sec:
        parts.append(caution_sec)
    text = "\n\n".join(parts)

    if len(text) <= max_chars:
        return text

    # 截断：优先保住时间序轨迹；线索仅用剩余预算追加到文末
    fixed = (
        "（结构化压缩轨迹：已按上限截断，优先保留时间序轨迹前部；"
        "文末线索若空间不足可能省略。）\n\n## 压缩轨迹\n"
    )
    tail_extra = ""
    for sec in (hint_sec, caution_sec):
        if sec:
            tail_extra += "\n\n" + sec

    # 先为文末线索预留至多 25% 预算，其余给轨迹
    reserve = min(len(tail_extra), max(0, (max_chars - len(fixed)) // 4)) if tail_extra else 0
    remain_for_traj = max(0, max_chars - len(fixed) - reserve)

    if len(traj_body) <= remain_for_traj:
        traj_out = traj_body
        used = len(fixed) + len(traj_out)
    else:
        head_n = int(remain_for_traj * 0.70)
        mid = "\n…(轨迹中部省略)\n"
        use = remain_for_traj - len(mid)
        head_n = max(0, min(head_n, use))
        tail_n = max(0, use - head_n)
        traj_out = traj_body[:head_n] + mid + traj_body[-tail_n:]
        used = len(fixed) + len(traj_out)

    out = fixed + traj_out
    leftover = max_chars - used
    if leftover > 80 and tail_extra:
        # 线索整体放不下则尽量塞，超限再截
        extra = tail_extra
        if len(extra) > leftover:
            extra = extra[: leftover - 1] + "…"
        out += extra
    return out
