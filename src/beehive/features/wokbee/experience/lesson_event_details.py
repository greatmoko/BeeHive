"""经验事件参数、结果、成功线索和重复指纹提取。"""
from __future__ import annotations
import json
import re

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
    r"(?:scripts|workspace|output|uploads|memory|references)/"
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
                for p in ("memory/", "output/", "uploads/")
            ):
                hints.append(f"写入 {path}")
        if name == "execute" and cmd:
            if re.search(r"\.(py|bat|cmd|ps1|sh)\b", cmd, re.I) or any(
                m in cmd.replace("\\", "/") for m in ("memory/",)
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
        if p.lower().startswith(("memory/", "output/", "uploads/")):
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
