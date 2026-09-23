"""运行事件提取与经验摘要。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from wokbee.core.credential_store import redact_obj, redact_text
def slice_latest_round(events: list | None) -> list:
    """只保留最新一轮日志：最后一个「会话结束」标记与其前一个标记之间的事件。

    结束标记（info 事件 + meta.session_end）在每轮运行/对话结束时写入，
    因此最新一轮 = 最后两个标记之间（仅一个标记时取其之前）。
    供总结经验时过滤旧轮日志，避免把历史轮次塞给 AI 占用 token。
    无标记时返回原列表（首轮或旧格式数据）。
    """
    if not events:
        return list(events or [])

    def _is_marker(ev) -> bool:
        meta = getattr(ev, "meta", None) or {}
        if not isinstance(meta, dict):
            meta = {}
        return getattr(ev, "kind", "") == "info" and meta.get("session_end")

    markers = [i for i, ev in enumerate(events) if _is_marker(ev)]
    if not markers:
        return list(events)
    if len(markers) >= 2:
        start = markers[-2] + 1
        end = markers[-1]
    else:
        start = 0
        end = markers[-1]
    return list(events[start:end])


def collect_events_log(events: list | None, *, max_chars: int = 20000) -> str:
    """把时间线事件压成日志文本（原文拼接，供 refine 等轻量场景）。

    消息 ID / 时间戳一律改为序数编号（1. 2. 3. …），便于 AI 引用。
    """
    lines: list[str] = []
    for ev in events or []:
        kind = getattr(ev, "kind", "") or ""
        content = (getattr(ev, "content", None) or "").strip()
        if not content:
            continue
        if kind in ("tool", "agent", "error", "user", "info", "approval", "lesson"):
            chunk = content if len(content) <= 1200 else content[:1200] + "…"
            lines.append(f"{len(lines) + 1}. {kind}: {chunk}")
    text = "\n".join(lines) if lines else "（无运行日志）"
    if len(text) > max_chars:
        text = "…(日志前部截断)\n" + text[-max_chars:]
    return text


# --------------------------------------------------------------------------- #
# 经验总结用结构化轨迹 Digest（压缩噪声、去重、成功路径前置）
# --------------------------------------------------------------------------- #

_RESULT_DIGEST_CHARS = 200
_AGENT_DIGEST_CHARS = 280
_ERROR_DIGEST_CHARS = 800
_SUCCESS_SECTION_MAX = 8000

_NAV_NOISE_RE = re.compile(
    r"(首页|导航|登录|注册|隐私|关于我们|copyright|cookie|订阅|菜单|"
    r"sitemap|footer|header|navbar|breadcrumb)",
    re.I,
)
_URL_RE = re.compile(r"https?://[^\s\]\"'<>]+", re.I)
_PATH_RE = re.compile(
    r"(?:scripts|workspace|deliverables|uploads|memory|references)/"
    r"[^\s\]\"'<>]+",
    re.I,
)
_MD_CALL_HEAD = re.compile(r"^\*\*call:\*\*\s*`([^`]+)`\s*", re.I)
_MD_CB_HEAD = re.compile(r"^\*\*callback:\*\*\s*`([^`]+)`\s*", re.I)
_ARG_LINE_RE = re.compile(r"^-\s+\*\*([^*]+):\*\*\s*(.*)$")


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _clip(text: str, max_chars: int) -> str:
    s = (text or "").strip()
    if len(s) <= max_chars:
        return s
    return s[: max(0, max_chars - 1)].rstrip() + "…"


def _strip_md_fence(body: str) -> str:
    t = (body or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[^\n]*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    return t.strip()


def _parse_tool_call_content(content: str) -> tuple[str, dict[str, str]]:
    """从时间线 Markdown call 正文解析 tool 名与关键参数。"""
    text = (content or "").strip()
    name = ""
    m = _MD_CALL_HEAD.match(text)
    if m:
        name = m.group(1).strip()
        text = text[m.end() :]
    args: dict[str, str] = {}
    for line in text.splitlines():
        am = _ARG_LINE_RE.match(line.strip())
        if not am:
            continue
        key = am.group(1).strip()
        val = am.group(2).strip()
        if key and val and key not in args:
            args[key] = val
    return name, args


def _parse_tool_callback_content(content: str) -> tuple[str, str]:
    text = (content or "").strip()
    name = ""
    m = _MD_CB_HEAD.match(text)
    if m:
        name = m.group(1).strip()
        text = text[m.end() :]
    return name, _strip_md_fence(text)


def _args_digest(args: dict | None, *, content_preview: int = 80) -> str:
    if not isinstance(args, dict) or not args:
        return ""
    preferred = (
        "url",
        "path",
        "file_path",
        "command",
        "query",
        "method",
        "label",
        "max_chars",
    )
    parts: list[str] = []
    keys = [k for k in preferred if k in args] + [
        k for k in args.keys() if k not in preferred and k not in ("content", "body", "text", "code")
    ]
    for k in keys[:8]:
        v = args.get(k)
        if v is None:
            continue
        if isinstance(v, (dict, list)):
            raw = json.dumps(v, ensure_ascii=False)
        else:
            raw = str(v)
        raw = _collapse_ws(raw)
        if len(raw) > 160:
            raw = raw[:160] + "…"
        parts.append(f"{k}={raw}")
    for heavy in ("content", "body", "text", "code"):
        if heavy in args and args.get(heavy) is not None:
            raw = _collapse_ws(str(args.get(heavy)))
            parts.append(f"{heavy}=({len(raw)}字){_clip(raw, content_preview)}")
            break
    return " | ".join(parts)


def _result_digest(body: str, *, status: str = "") -> str:
    raw = _collapse_ws(_strip_md_fence(body))
    # 去掉导航噪声占主导时，尽量保留后面更有信息量的片段
    if len(raw) > 80 and _NAV_NOISE_RE.search(raw[:120]):
        mid = raw[len(raw) // 4 : len(raw) // 4 + _RESULT_DIGEST_CHARS * 2]
        mid_c = _collapse_ws(mid)
        # 中段若几乎是同一字符（截断填充），仍用去噪后的前部
        if len(mid_c) > 40 and len(set(mid_c[:80])) > 4:
            raw = mid_c
        else:
            raw = _NAV_NOISE_RE.sub(" ", raw)
            raw = _collapse_ws(raw)
    st = (status or "").strip().lower()
    if not st:
        low = raw.lower()
        if any(x in low for x in ("error", "traceback", "失败", "【循环检测】", "blocked")):
            st = "failed"
        elif not raw or raw in ("（无输出）", "(无输出)"):
            st = "empty"
        else:
            st = "ok"
    return f"{st} | len={len(body or '')} | {_clip(raw, _RESULT_DIGEST_CHARS)}"


def _is_successish_status(status: str) -> bool:
    s = (status or "").strip().lower()
    return s in ("", "ok", "success", "succeeded", "done")


def _collect_success_hints(
    tool: str,
    phase: str,
    args: dict | None,
    body: str,
    status: str,
) -> list[str]:
    """从单次工具事件提炼无序检索线索（URL/路径等），非执行顺序。"""
    hints: list[str] = []
    name = (tool or "").strip()
    if not name:
        return hints
    args = args if isinstance(args, dict) else {}

    if phase == "call":
        path = str(args.get("file_path") or args.get("path") or "").strip()
        cmd = str(args.get("command") or "").strip()
        url = str(args.get("url") or "").strip()
        query = str(args.get("query") or "").strip()
        if name in ("write_file", "edit_file") and path:
            if any(
                path.replace("\\", "/").startswith(p)
                for p in ("scripts/", "deliverables/", "workspace/", "uploads/", "references/")
            ):
                hints.append(f"写入 {path}")
        if name == "execute" and cmd:
            if re.search(r"\.(py|bat|cmd|ps1|sh)\b", cmd, re.I) or "scripts/" in cmd.replace(
                "\\", "/"
            ):
                hints.append(f"execute → {_clip(cmd, 120)}")
        if name in ("http_get", "http_request") and url:
            hints.append(f"{name} {url}")
        if name in ("web_search", "deepseek_web_search") and query:
            hints.append(f"{name} q={_clip(query, 80)}")
        return hints

    if phase == "callback" and not _is_successish_status(status):
        return hints

    # callback：从正文抠路径/URL（补充 meta 缺失时）
    for p in _PATH_RE.findall(body or ""):
        if p.lower().startswith(("scripts/", "deliverables/", "uploads/", "references/")):
            hints.append(f"产物/脚本 {p}")
    if name in ("http_get", "http_request", "web_search", "deepseek_web_search"):
        for u in _URL_RE.findall(str(args.get("url") or ""))[:1]:
            hints.append(f"已请求 {u}")
    if "script_callback_" in (body or ""):
        for p in _PATH_RE.findall(body or ""):
            if "script_callback_" in p:
                hints.append(f"callback 落盘 {p}")
    return hints


def _fingerprint_line(kind: str, tool: str, phase: str, args_s: str, result_s: str) -> str:
    return f"{kind}|{tool}|{phase}|{args_s}|{result_s[:120]}"


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
