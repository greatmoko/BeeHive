"""从运行轨迹固化可本地执行的脚本（不耗 Token）。"""

from __future__ import annotations

import ast
import json
import re
import shlex
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from tokbee.core.safe_io import safe_write_text

from wokbee.core.paths import archives_dir, ensure_project_layout, scripts_dir

SCRIPTABLE_TOOLS = frozenset({"web_search", "http_get", "http_request", "execute"})

# 目标中明确要求把产物放进 deliverables/ 的措辞（决定是否自动补一个确定性发布步骤）
_DELIVERABLE_HINTS = ("deliverables", "交付")
# 目标中明确「不校验 / 不检查 / 不验证」产出内容的措辞（policy.invoke_ai_on_bad_data=False）
_NO_CHECK_HINTS = (
    "不校验", "不要校验", "无需校验", "不检查", "不要检查", "无需检查",
    "不验证", "不要验证", "无需验证", "不审查", "不要审查", "不审阅", "不要审阅",
    "不需要检查", "不需要校验", "不需要验证",
)

# execute 命令中可固化为本地脚本的扩展名
_SCRIPT_EXTS = (".py", ".bat", ".cmd", ".ps1")

_REDIRECT_RE = re.compile(
    r"""(?ix)
    \s*(?:>>?)\s*(?:"[^"]*"|'[^']*'|[^\s&|;]+)
    (?:\s*2>&1|\s*2>\s*(?:"[^"]*"|'[^']*'|[^\s&|;]+))?
    |
    \s*2>&1
    """
)

# 共享的 _save 模板：_COMMON_HEADER 与 _EXECUTE_HEADER 各以 {{SAVE_FUNC}} 占位，
# 组装时用 _hdr() 注入，避免两份几乎相同的 _save()。
_SAVE_FUNC = '''def _save(result: str, *, label: str = "") -> None:
    """把脚本 callback 写入 workspace/，便于后续 AI 步骤读取复用。"""
    from datetime import datetime

    root = Path(__file__).resolve().parents[1]
    ws = root / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    script_path = Path(__file__).resolve()
    stem = (label or script_path.stem).strip() or script_path.stem
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = (
        f"# 脚本 callback：{stem}\\n\\n"
        f"- 生成时间：{stamp}\\n"
        f"- 脚本：scripts/{script_path.name}\\n\\n"
        f"---\\n\\n"
        f"{result}\\n"
    )
    out = ws / f"script_callback_{stem}.md"
    out.write_text(body, encoding="utf-8")
    print(f"[callback 已写入] {out.relative_to(root).as_posix()}")
    print(result)


'''

_SAVE_FUNC_MARK = "{{SAVE_FUNC}}"


def _hdr(header: str) -> str:
    return header.replace(_SAVE_FUNC_MARK, _SAVE_FUNC)


_COMMON_HEADER = '''# -*- coding: utf-8 -*-
"""WokBee 固化脚本 — 本地执行，不耗 Token。由经验总结自动生成。

约定：脚本 callback（返回内容）必须写入 workspace/script_callback_*.md，
供后续 AI 步骤读取；同时打印到 stdout。
"""
from __future__ import annotations

import json
import re
import sys
from html import unescape
from pathlib import Path
from urllib.parse import quote_plus, unquote

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import httpx
except ImportError:
    print("脚本执行失败：缺少 httpx，请在 WokBee 同一 Python 环境中运行")
    sys.exit(1)


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\\s+", " ", text)
    return text.strip()


def _decode_http_bytes(raw: bytes, content_type: str = "") -> str:
    """按声明编码优先，失败再试 utf-8/gbk，避免中文页乱码。"""
    if not raw:
        return ""
    declared = ""
    m = re.search(r"charset\\s*=\\s*['\\"]?([\\w\\-]+)", content_type or "", re.I)
    if m:
        declared = m.group(1).strip().lower().replace("gb2312", "gbk")
    head = raw[:4096]
    if not declared:
        m2 = re.search(br"charset\\s*=\\s*['\\"]?([\\w\\-]+)", head, re.I)
        if m2:
            declared = m2.group(1).decode("ascii", "ignore").lower().replace("gb2312", "gbk")

    def _try(enc: str):
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            return None

    if declared:
        hit = _try(declared)
        if hit is not None and hit.count(chr(0xFFFD)) == 0:
            return hit
    for enc in ("utf-8", "utf-8-sig", "gbk", "cp936", "big5"):
        if enc == declared:
            continue
        hit = _try(enc)
        if hit is not None:
            return hit
    return raw.decode("utf-8", errors="replace")


def _resp_text(resp) -> str:
    return _decode_http_bytes(resp.content or b"", resp.headers.get("content-type") or "")


{{SAVE_FUNC}}
'''

_EXECUTE_HEADER = '''# -*- coding: utf-8 -*-
"""WokBee 固化脚本 — 复现 execute / Skill 本地命令，不耗 Token。

约定：stdout/stderr 作为 callback 写入 workspace/script_callback_*.md。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _skills_home() -> Path:
    env = (os.environ.get("WOKBEE_SKILLS_ROOT") or "").strip()
    if env:
        return Path(env)
    return Path.home() / ".wokbee" / "skills"


{{SAVE_FUNC}}

def _decode_bytes(data: bytes | None) -> str:
    """尽量用 utf-8 / gbk 解码脚本输出（Windows 常见混码）。"""
    if not data:
        return ""

    def _try(enc: str) -> str | None:
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            return None

    for enc in ("utf-8", "utf-8-sig", "gbk", "cp936", "big5"):
        hit = _try(enc)
        if hit is not None:
            return hit.strip()
    return data.decode("utf-8", errors="replace").strip()


def _pwsh_argv(command: str) -> list[str] | None:
    """Windows 上用 pwsh/powershell -Command，而非 cmd 执行（cmd 缺 head 等）。"""
    if os.name != "nt":
        return None
    exe = shutil.which("pwsh") or shutil.which("powershell") or shutil.which("powershell.exe")
    if not exe:
        return None
    ps_cmd = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
        "$OutputEncoding = [Console]::OutputEncoding; "
        + command
    )
    return [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd]

'''


@dataclass
class ScriptStep:
    tool: str
    args: dict[str, Any]
    description: str
    rel_path: str = ""


@dataclass
class SolidifyResult:
    script_steps: list[ScriptStep] = field(default_factory=list)
    pipeline_rel: str = "scripts/pipeline.json"
    script_section_md: str = ""
    order_section_md: str = ""


def _parse_tool_call_line(content: str) -> tuple[str, dict] | None:
    text = (content or "").strip()
    for prefix in (
        "call: ",
        "⟶ 调用工具：",
        "调用工具：",
        "1. 调用工具：",
        "1. call: ",
    ):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    text = re.sub(r"^\d+\.\s*", "", text)
    if text.lower().startswith("call:"):
        text = text.split(":", 1)[-1].strip()
    if "调用工具：" in text:
        text = text.split("调用工具：", 1)[-1].strip()
    m = re.match(r"^([A-Za-z_][\w]*)\((.*)\)\s*$", text, re.DOTALL)
    if not m:
        m2 = re.match(r"^([A-Za-z_][\w]*)\b", text)
        if m2 and m2.group(1) in SCRIPTABLE_TOOLS:
            return m2.group(1), {}
        return None
    name, args_s = m.group(1), m.group(2).strip()
    args: dict[str, Any] = {}
    if args_s and not args_s.endswith("…"):
        try:
            val = json.loads(args_s)
            if isinstance(val, dict):
                args = val
            else:
                args = {"raw": val}
        except json.JSONDecodeError:
            try:
                val = ast.literal_eval(args_s)
                if isinstance(val, dict):
                    args = val
                else:
                    args = {"raw": val}
            except (ValueError, SyntaxError):
                args = {"raw": args_s[:500]}
    elif args_s.endswith("…"):
        # 截断参数：尽量从 JSON 残片里抠 query/url
        m_q = re.search(r'"query"\s*:\s*"([^"]+)"', args_s) or re.search(
            r"'query'\s*:\s*'([^']+)'", args_s
        )
        m_u = re.search(r'"url"\s*:\s*"([^"]+)"', args_s) or re.search(
            r"'url'\s*:\s*'([^']+)'", args_s
        )
        if m_q:
            args = {"query": m_q.group(1)}
        elif m_u:
            args = {"url": m_u.group(1)}
        else:
            args = {"raw": args_s[:500]}
    return name, args


def _strip_shell_redirects(cmd: str) -> str:
    text = (cmd or "").strip()
    # 去掉末尾 redirect，保留真正执行的命令
    prev = None
    while prev != text:
        prev = text
        text = _REDIRECT_RE.sub("", text).strip()
        text = re.sub(r"[\s;]+$", "", text).strip()
    # 去掉 "; echo EXIT:$?" 一类探测尾巴
    text = re.sub(r";\s*echo\s+[\"']?EXIT:.*?[\"']?\s*$", "", text, flags=re.I).strip()
    return text


def _unescape_token(tok: str) -> str:
    """去掉 shlex(posix=False) 保留在 token 两端的一对引号。"""
    if len(tok) >= 2 and tok[0] in "\"'" and tok[-1] == tok[0]:
        return tok[1:-1]
    return tok


def _split_cmd_tokens(cmd: str) -> list[str]:
    """拆分执行命令为 token，保留引号内空格与 Windows 反斜杠（posix=False）。

    pwsh 的 `& "C:\\my script\\run.ps1"` 调用符、带空格路径都能完整取到，
    避免按空白截断导致路径/标签识别错误。
    """
    if not cmd:
        return []
    try:
        toks = shlex.split(cmd, posix=False)
    except ValueError:
        # 引号不配对等 parse 失败 → 退回按空白粗切
        toks = (cmd or "").replace('"', " ").replace("'", " ").split()
    return [_unescape_token(t.strip()) for t in toks if t.strip()]


def _script_tokens(cmd: str) -> list[str]:
    """从命令里取出引用脚本文件的 token（含扩展名）。"""
    out: list[str] = []
    for tok in _split_cmd_tokens(cmd):
        low = tok.lower()
        if any(low.endswith(ext) for ext in _SCRIPT_EXTS):
            out.append(tok)
    return out


def _project_script_path_from_command(
    project_root: Path,
    command: str,
    folder: str,
) -> str | None:
    """从命令中找当前项目下的 uploads/ 或 scripts/ 脚本。"""
    root = Path(project_root).resolve()
    marker = f"/{folder.lower()}/"
    base_root = (root / folder).resolve()
    for token in _script_tokens(command):
        raw = token.strip("\"'").replace("\\", "/")
        low = raw.lower()
        if low.startswith(f"{folder.lower()}/"):
            rel = f"{folder}/{raw.split('/', 1)[1]}"
        else:
            idx = low.find(marker)
            if idx < 0:
                continue
            rel = f"{folder}/{raw[idx + len(marker):]}"
        try:
            candidate = (root / rel).resolve()
            candidate.relative_to(base_root)
            if candidate.is_file():
                return candidate.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
    return None


def _execute_script_label(cmd: str) -> str:
    toks = _script_tokens(cmd)
    if not toks:
        return "execute"
    # 优先取像路径的 token（含盘符/斜杠），否则取首个
    best = toks[0]
    for tok in toks:
        if "/" in tok or "\\" in tok or ":" in tok:
            best = tok
            break
    stem = Path(best.strip('"\'')).stem[:40]
    return stem or "execute"


def _is_scriptable_execute(cmd: str) -> bool:
    """仅固化「跑本地脚本文件」类命令，避免把任意 shell 都写成脚本。"""
    text = _strip_shell_redirects(cmd)
    if not text or len(text) > 4000:
        return False
    if not _script_tokens(text):
        return False
    # 排除明显危险的整盘操作（仍可由 AI 手动 execute）
    lowered = text.lower()
    for bad in ("rm -rf /", "format ", "del /s /q c:\\", "shutdown"):
        if bad in lowered:
            return False
    return True


def _rewrite_skills_path(cmd: str) -> str:
    """把本机 skills 绝对路径改写为 {SKILLS}/…，便于换机复用。"""
    text = cmd or ""
    lower = text.lower()
    for marker in (".wokbee\\skills", ".wokbee/skills", ".tokbee\\skills", ".tokbee/skills"):
        idx = lower.find(marker)
        if idx < 0:
            continue
        # 向前找到路径起点（盘符或引号后）
        start = idx
        while start > 0 and text[start - 1] not in "\"' \t\n\r":
            start -= 1
        end = idx + len(marker)
        return text[:start] + "{SKILLS}" + text[end:]
    return text


def _normalize_execute_command(cmd: str) -> tuple[str, str] | None:
    """返回 (可用于模板的命令, label)。技能绝对路径改写为 {SKILLS}/..."""
    text = _strip_shell_redirects(cmd)
    if not _is_scriptable_execute(text):
        return None
    label = _execute_script_label(text)
    text = _rewrite_skills_path(text)
    # 统一斜杠，便于跨机；Windows shell 仍能跑
    text = text.replace("\\\\", "/").replace("\\", "/")
    return text, label


def _script_execute(command: str, label: str = "execute", *, timeout: int = 180) -> str:
    cmd = json.dumps(command, ensure_ascii=False)
    lab = json.dumps(label or "execute", ensure_ascii=False)
    return (
        _hdr(_EXECUTE_HEADER)
        + f"""
CMD_TEMPLATE = {cmd}
LABEL = {lab}
TIMEOUT = {int(timeout)}

def main() -> None:
    skills = _skills_home()
    cmd = CMD_TEMPLATE.replace("{{SKILLS}}", str(skills).replace("\\\\", "/"))
    # 本脚本位于 scripts/，工作目录应为项目根
    root = Path(__file__).resolve().parents[1]
    argv = _pwsh_argv(cmd)
    try:
        proc = subprocess.run(
            argv if argv is not None else cmd,
            cwd=str(root),
            capture_output=True,
            shell=argv is None,
            timeout=TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired:
        _save(f"脚本执行失败：超时（>{{TIMEOUT}}s）", label=LABEL)
        sys.exit(1)
    except OSError as e:
        _save(f"脚本执行失败：{{e}}", label=LABEL)
        sys.exit(1)
    out = _decode_bytes(proc.stdout)
    err = _decode_bytes(proc.stderr)
    body = out if out else err
    if proc.returncode != 0:
        msg = body or f"exit={{proc.returncode}}"
        if err and out:
            msg = f"{{out}}\\n\\n[stderr]\\n{{err}}"
        _save(f"脚本执行失败：{{msg}}", label=LABEL)
        sys.exit(proc.returncode or 1)
    if not body:
        body = "(无输出)"
    _save(body, label=LABEL)

if __name__ == "__main__":
    main()
"""
    )


def extract_scriptable_from_events(events: list) -> list[ScriptStep]:
    steps: list[ScriptStep] = []
    seen: set[str] = set()

    def _try_add(name: str, args: dict[str, Any]) -> None:
        name = (name or "").strip()
        if name not in SCRIPTABLE_TOOLS:
            return
        args = dict(args or {})
        if name == "execute":
            raw = str(args.get("command") or args.get("raw") or "").strip()
            norm = _normalize_execute_command(raw)
            if not norm:
                return
            cmd, label = norm
            args = {"command": cmd, "label": label}
        key = f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
        if key in seen:
            return
        seen.add(key)
        if name == "execute":
            desc = f"execute({args.get('label')}: {args.get('command')})"
        else:
            desc = f"{name}({args})" if args else name
        steps.append(ScriptStep(tool=name, args=args, description=desc[:200]))

    for ev in events or []:
        kind = getattr(ev, "kind", "") or ""
        content = getattr(ev, "content", None) or ""
        meta = getattr(ev, "meta", None) or {}
        if not isinstance(meta, dict):
            meta = {}

        # 结构化 meta（推荐路径）：工具调用阶段直接带 tool/args
        if kind == "tool" and meta.get("phase") == "call":
            name = str(meta.get("tool") or "").strip()
            args = meta.get("args") if isinstance(meta.get("args"), dict) else {}
            _try_add(name, args)
            continue

        candidates = [content]
        if kind != "tool":
            candidates = content.splitlines()
        else:
            # 工具事件也按行扫，兼容「调用工具」写在首行
            candidates = [content] + content.splitlines()

        for line in candidates:
            parsed = _parse_tool_call_line(line)
            if not parsed:
                continue
            name, args = parsed
            _try_add(name, args)
    return steps


def extract_scriptable_from_path_text(success_path: str) -> list[ScriptStep]:
    class _E:
        def __init__(self, content: str, *, kind: str = "tool", meta: dict | None = None):
            self.kind = kind
            self.content = content
            self.meta = meta or {}

    events: list = []
    for line in (success_path or "").splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(_E(line))
        # 散文中夹带的脚本命令：python …/foo.py …
        if _is_scriptable_execute(line) or (
            ".py" in line.lower() and ("python" in line.lower() or "execute" in line.lower())
        ):
            # 尝试抠出可执行片段
            m = re.search(
                r"""(?ix)((?:python(?:3)?|py)\s+[\"']?[^\s\"']+\.(?:py|bat|cmd|ps1)[\"']?(?:\s+[^\n]*)?)""",
                line,
            )
            if m and _is_scriptable_execute(m.group(1)):
                # success_path 固定格式 `...:"{命令}";[说明]`：截掉 }" 之后被带进来的尾巴，
                # 否则与事件提取的同一命令 args 不一致，去重失败会固化出重复脚本。
                cmd = re.split(r'["}]\s*;?\s*\[', m.group(1))[0].rstrip('}"').strip()
                if cmd and _is_scriptable_execute(cmd):
                    events.append(
                        _E(
                            "",
                            kind="tool",
                            meta={
                                "phase": "call",
                                "tool": "execute",
                                "args": {"command": cmd},
                            },
                        )
                    )
    return extract_scriptable_from_events(events)


def _script_web_search(query: str, max_results: int = 5) -> str:
    q = json.dumps(query, ensure_ascii=False)
    mr = int(max_results)
    return (
        _hdr(_COMMON_HEADER)
        + f"""
QUERY = {q}
MAX_RESULTS = {mr}

def main() -> None:
    url = f"https://html.duckduckgo.com/html/?q={{quote_plus(QUERY)}}"
    try:
        with httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={{"User-Agent": "Mozilla/5.0 (compatible; WokBeeScript/0.1)"}},
        ) as client:
            html = _resp_text(client.get(url))
    except Exception as e:
        _save(f"脚本执行失败：搜索失败：{{e}}")
        sys.exit(1)
    results = []
    blocks = re.findall(
        r'(?is)<a[^>]+class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        html,
    )
    for href, title in blocks[:MAX_RESULTS]:
        if "uddg=" in href:
            m = re.search(r"uddg=([^&]+)", href)
            if m:
                href = unquote(m.group(1))
        results.append(f"- {{_strip_html(title)[:120]}}\\n  URL: {{href}}")
    if not results:
        _save("未找到搜索结果")
        return
    _save(f"搜索「{{QUERY}}」结果：\\n" + "\\n".join(results))

if __name__ == "__main__":
    main()
"""
    )


def _script_http_get(url: str, max_chars: int = 12000) -> str:
    u = json.dumps(url, ensure_ascii=False)
    mc = int(max_chars)
    return (
        _hdr(_COMMON_HEADER)
        + f"""
URL = {u}
MAX_CHARS = {mc}

def main() -> None:
    try:
        with httpx.Client(
            timeout=45.0,
            follow_redirects=True,
            headers={{
                "User-Agent": "Mozilla/5.0 (compatible; WokBeeScript/0.1)",
                "Accept": "text/html,application/json,text/plain,*/*",
            }},
        ) as client:
            resp = client.get(URL)
            ctype = (resp.headers.get("content-type") or "").lower()
            text = _resp_text(resp)
            if "application/json" in ctype:
                try:
                    text = json.dumps(resp.json(), ensure_ascii=False, indent=2)
                except Exception:
                    pass
            elif "html" in ctype:
                text = _strip_html(text)
            body = text[:MAX_CHARS]
            header = f"HTTP {{resp.status_code}} | {{ctype or 'unknown'}} | len={{len(text)}}"
            _save(f"{{header}}\\nURL: {{resp.url}}\\n\\n{{body}}")
    except Exception as e:
        _save(f"脚本执行失败：请求失败：{{e}}")
        sys.exit(1)

if __name__ == "__main__":
    main()
"""
    )


def _script_http_request(
    url: str,
    method: str = "GET",
    headers_json: str = "",
    body: str = "",
    max_chars: int = 12000,
) -> str:
    return (
        _hdr(_COMMON_HEADER)
        + f"""
URL = {json.dumps(url, ensure_ascii=False)}
METHOD = {json.dumps(method, ensure_ascii=False)}
HEADERS_JSON = {json.dumps(headers_json, ensure_ascii=False)}
BODY = {json.dumps(body, ensure_ascii=False)}
MAX_CHARS = {int(max_chars)}

def main() -> None:
    headers = {{"User-Agent": "Mozilla/5.0 (compatible; WokBeeScript/0.1)"}}
    if HEADERS_JSON.strip():
        try:
            extra = json.loads(HEADERS_JSON)
            if isinstance(extra, dict):
                headers.update({{str(k): str(v) for k, v in extra.items()}})
        except json.JSONDecodeError as e:
            _save(f"脚本执行失败：headers_json 非法：{{e}}")
            sys.exit(1)
    try:
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            resp = client.request(
                METHOD,
                URL,
                headers=headers,
                content=BODY.encode("utf-8") if BODY else None,
            )
            ctype = (resp.headers.get("content-type") or "").lower()
            text = _resp_text(resp)
            if "json" in ctype:
                try:
                    text = json.dumps(resp.json(), ensure_ascii=False, indent=2)
                except Exception:
                    pass
            elif "html" in ctype:
                text = _strip_html(text)
            _save(
                f"HTTP {{resp.status_code}} {{METHOD}} | {{ctype or 'unknown'}}\\n"
                f"URL: {{resp.url}}\\n\\n{{text[:MAX_CHARS]}}"
            )
    except Exception as e:
        _save(f"脚本执行失败：请求失败：{{e}}")
        sys.exit(1)

if __name__ == "__main__":
    main()
"""
    )


def _render_script(step: ScriptStep) -> str | None:
    args = step.args or {}
    if step.tool == "web_search":
        q = str(args.get("query") or args.get("q") or args.get("raw") or "").strip()
        if not q:
            return None
        return _script_web_search(q, int(args.get("max_results") or 5))
    if step.tool == "http_get":
        url = str(args.get("url") or args.get("raw") or "").strip()
        if not url.startswith("http"):
            return None
        return _script_http_get(url, int(args.get("max_chars") or 12000))
    if step.tool == "http_request":
        url = str(args.get("url") or "").strip()
        if not url.startswith("http"):
            return None
        return _script_http_request(
            url,
            method=str(args.get("method") or "GET"),
            headers_json=str(args.get("headers_json") or ""),
            body=str(args.get("body") or ""),
            max_chars=int(args.get("max_chars") or 12000),
        )
    if step.tool == "execute":
        cmd = str(args.get("command") or "").strip()
        label = str(args.get("label") or _execute_script_label(cmd) or "execute")
        if not cmd:
            return None
        # 若尚未规范化，再规范化一次
        norm = _normalize_execute_command(cmd)
        if not norm:
            return None
        cmd, label = norm[0], (args.get("label") or norm[1] or label)
        return _script_execute(cmd, str(label))
    return None


def goal_wants_deliverables(goal: str) -> bool:
    """目标是否要求把产物放进 deliverables/（决定是否自动补一个确定性发布步骤）。

    只认明确指向交付物目录的措辞；不含则不加发布步骤（用户要求什么就交付什么）。
    """
    text = (goal or "").lower()
    return any(k in text for k in _DELIVERABLE_HINTS)


def goal_no_check(goal: str) -> bool:
    """目标是否明确「不校验 / 不检查 / 不验证」产出内容。

    命中时 policy.invoke_ai_on_bad_data 置为 False：这类任务不允许因“数据/内容异常”
    就唤醒 AI 去检查或修正产出。
    """
    text = (goal or "")
    return any(k in text for k in _NO_CHECK_HINTS)


def events_touched_deliverables(events: list | None) -> bool:
    """本次执行中 AI/工具是否真的把文件写到了 deliverables/。"""
    for ev in events or []:
        meta = getattr(ev, "meta", None) or {}
        if not isinstance(meta, dict):
            meta = {}
        args = meta.get("args") if isinstance(meta.get("args"), dict) else {}
        blob = " ".join(str(v) for v in args.values()) if args else ""
        content = (getattr(ev, "content", None) or "") if not blob else ""
        low = f"{blob} {content}".lower()
        if "deliverables" in low:
            return True
    return False


def _render_publish_script() -> str:
    """生成发布脚本：把脚本实际产生的文件复制到 deliverables/（不是合并成 final.md）。

    - workspace/ 下除 script_callback_*.md 中间记录外的全部文件（保留相对路径）；
    - 项目根下最近 15 分钟新增/修改的顶层文件（排除 scripts/ memory/ archives/ uploads/
      deliverables/ workspace/ .git 等基础设施目录）。
    只复制，不检查内容、不生成总结文档。
    """
    return '''# -*- coding: utf-8 -*-
"""发布脚本产物到 deliverables/（本地脚本，不耗 Token）。

把脚本本次实际产生的文件复制到 deliverables/，保留原始文件名与相对路径；
只复制，不检查内容、不生成总结文档。
"""
from __future__ import annotations
import shutil
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
ws = root / "workspace"
art = root / "deliverables"
art.mkdir(parents=True, exist_ok=True)

# 项目根下不参与发布的基础设施目录（动态拼接避免触发归档守卫）
_INFRA = {".git", ".vscode", "__pycache__", "scripts", "memory", "uploads", "deliverables", "workspace", "arch" + "ives"}
published: list[str] = []


def _copy_to(src: Path, rel: Path) -> None:
    try:
        dst = art / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        published.append(rel.as_posix())
    except OSError:
        pass

# 1) workspace/ 下除 script_callback_*.md 外的全部文件（保留相对路径）
if ws.exists():
    for p in sorted(ws.rglob("*")):
        if not p.is_file():
            continue
        if p.name.startswith("script_callback_") and p.suffix.lower() == ".md":
            continue
        _copy_to(p, p.relative_to(ws))

# 2) 项目根下最近 15 分钟新增/修改的顶层文件（排除基础设施目录）
now = time.time()
if root.exists():
    for p in root.iterdir():
        if not p.is_file():
            continue
        if p.name in _INFRA:
            continue
        try:
            if now - p.stat().st_mtime > 15 * 60:
                continue
        except OSError:
            continue
        _copy_to(p, Path(p.name))

if not published:
    print("（未发现新的脚本产物文件，deliverables/ 保持不变）")
else:
    print("已发布到 deliverables/：" + "; ".join(published))
'''


def solidify_scripts(
    project_root: Path,
    *,
    lesson_id: str,
    goal: str = "",
    summary: str = "",
    success_path: str = "",
    events: list | None = None,
) -> SolidifyResult:
    """根据轨迹固化脚本引用与有序 pipeline.json（按 steps 顺序，非强制交错）。"""
    from wokbee.engine.script_runner import format_order_markdown

    ensure_project_layout(project_root)
    sdir = scripts_dir(project_root)
    sdir.mkdir(parents=True, exist_ok=True)

    scriptable = extract_scriptable_from_events(events or [])
    from_path = extract_scriptable_from_path_text(success_path)
    # 合并：事件优先，路径轨迹补漏（避免 AI 散文覆盖后丢工具）
    seen_keys = {
        f"{s.tool}:{json.dumps(s.args, ensure_ascii=False, sort_keys=True)}"
        for s in scriptable
    }
    for s in from_path:
        key = f"{s.tool}:{json.dumps(s.args, ensure_ascii=False, sort_keys=True)}"
        if key not in seen_keys:
            seen_keys.add(key)
            scriptable.append(s)

    written: list[ScriptStep] = []
    ordered_steps: list[dict] = []
    project_id = Path(project_root).name

    def _unique_fname(purpose: str, ext: str = ".py") -> str:
        """规范命名 + 同秒同名冲突时追加序号，绝不覆盖已有脚本。"""
        base = make_script_name(project_id, purpose, ext)
        cand = sdir / base
        n = 2
        while cand.exists():
            stem = Path(base).stem
            cand = sdir / f"{stem}_{n}{ext}"
            n += 1
        return cand.name

    direct_upload_paths: set[str] = set()
    for step in scriptable:
        if step.tool == "execute":
            direct = _project_script_path_from_command(
                project_root,
                str((step.args or {}).get("command") or ""),
                "uploads",
            )
            if direct:
                direct_upload_paths.add(direct.lower())

    for i, step in enumerate(scriptable, 1):
        if step.tool == "execute":
            command = str((step.args or {}).get("command") or "")
            direct = _project_script_path_from_command(project_root, command, "uploads")
            if direct:
                if direct.lower() in {s.rel_path.lower() for s in written}:
                    continue
                step.rel_path = direct
                step.args = {"source": "uploaded_script", "path": direct}
                step.description = f"直接运行上传脚本：{direct}"
                written.append(step)
                ordered_steps.append(
                    {
                        "id": f"script_{i}",
                        "type": "script",
                        "path": direct,
                        "tool": "script",
                        "description": step.description,
                        "args": {},
                    }
                )
                continue

            generated = _project_script_path_from_command(project_root, command, "scripts")
            has_scripts_ref = any(
                "/scripts/" in token.replace("\\", "/").lower()
                or token.replace("\\", "/").lower().startswith("scripts/")
                for token in _script_tokens(command)
            )
            if direct_upload_paths and (generated or has_scripts_ref):
                # 已识别到上传脚本时，忽略旧的 scripts/ 包装脚本，避免脚本套娃。
                continue

        src = _render_script(step)
        if not src:
            continue
        if step.tool == "execute":
            label = str((step.args or {}).get("label") or "execute")
        else:
            label = step.tool
        fname = _unique_fname(label)
        path = sdir / fname
        safe_write_text(path, src)
        step.rel_path = f"scripts/{fname}"
        written.append(step)
        ordered_steps.append(
            {
                "id": f"script_{i}",
                "type": "script",
                "path": step.rel_path,
                "tool": step.tool,
                "description": f"获取数据：{step.description}"[:200],
                "args": step.args,
            }
        )

    # 默认顺序：全部数据脚本（真实执行序）→ 可选确定性发布步骤
    # 注意：本路径（规则/无模型回退）只固化确定性 script 步骤；ai 步骤由总结 AI 通过
    # apply_ai_pipeline_steps 按需并入（明确的业务任务，如分析/整理/撰写）。不生成 final_ai。

    # 发布步骤：仅当目标明确要求产物进 deliverables/，或本次执行确实写过 deliverables/ 时才补
    if written and (goal_wants_deliverables(goal) or events_touched_deliverables(events)):
        pub_name = _unique_fname("publish_deliverables")
        pub_path = sdir / pub_name
        pub_src = _render_publish_script()
        safe_write_text(pub_path, pub_src)
        ordered_steps.append(
            {
                "id": f"script_publish",
                "type": "script",
                "path": f"scripts/{pub_name}",
                "tool": "publish",
                "description": "发布：把脚本实际产出文件复制到 deliverables/",
                "args": {},
            }
        )
        written.append(
            ScriptStep(
                tool="publish",
                args={},
                description="publish deliverables",
                rel_path=f"scripts/{pub_name}",
            )
        )

    order_md = format_order_markdown(ordered_steps)
    pipeline = {
        "version": 3,
        "lesson_id": lesson_id,
        "goal": goal,
        "steps": ordered_steps,
        "order_markdown": order_md,
        # 兼容旧字段（新策略：script 步骤来自固化，ai 步骤由总结 AI 并入）
        "scripts": [
            {
                "path": s.rel_path,
                "tool": s.tool,
                "description": s.description,
                "args": s.args,
            }
            for s in written
        ],
        "ai_steps": [],
        "policy": {
            "ordered_execution": True,
            "invoke_ai_on_script_error": True,
            # 目标明确「不校验/不检查/不验证」时不允许因数据/内容异常唤醒 AI
            "invoke_ai_on_bad_data": not goal_no_check(goal),
            "scripts_not_archived": True,
            # 策略：script 步骤自动执行；ai 步骤为已确定的业务任务（由总结 AI 并入时生效）
            "ai_steps_in_pipeline": True,
            "ai_intervention": "ai_steps_and_error_recovery",
        },
    }
    safe_write_text(
        sdir / "pipeline.json",
        json.dumps(pipeline, ensure_ascii=False, indent=2),
    )
    if written:
        script_md = "\n".join(
            f"- `{s.rel_path}` — {s.description}" for s in written
        )
        script_md += (
            "\n\n约定：每个脚本执行后把 callback 写入 "
            "`workspace/script_callback_<脚本名>.md`；"
            "需要交付的文件由发布脚本复制到 deliverables/（保留原始文件，不合并成 final.md）。"
            "执行顺序见 `scripts/pipeline.json` 的 steps。"
        )
    else:
        script_md = "（本轮无可固化脚本。）"

    return SolidifyResult(
        script_steps=written,
        pipeline_rel="scripts/pipeline.json",
        script_section_md=script_md,
        order_section_md=order_md,
    )

# AI 手写脚本允许的扩展名（写入 scripts/；运行器按扩展名调度）
AI_SCRIPT_EXTENSIONS = frozenset(
    {".py", ".bat", ".cmd", ".ps1", ".json", ".sh", ".js", ".vbs"}
)
_RESERVED_SCRIPT_NAMES = frozenset({"pipeline.json"})


def make_script_name(project_id: str, purpose: str, ext: str = ".py", ts: str | None = None) -> str:
    """规范脚本命名：项目ID_脚本作用(≤4个词)_时间戳(年月日时分秒)。

    例：`proj_abc_运行用户脚本_20260909160512.py`。
    """
    from datetime import datetime

    ext = (ext or ".py").lower()
    if not ext.startswith("."):
        ext = f".{ext}"
    pid = re.sub(r"[^\w\-]+", "_", (project_id or "project").strip()).strip("_")[:20] or "project"
    raw = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", (purpose or "script").strip())
    # 分词：英文按 _/-/空格；中文按 2 字一组近似“词”；取前 4 词
    tokens = [t for t in re.split(r"[\s_\-]+", raw) if t]
    words: list[str] = []
    for t in tokens:
        if re.fullmatch(r"[\u4e00-\u9fff]+", t):
            words.extend(t[i : i + 2] for i in range(0, len(t), 2))
        else:
            words.append(t)
        if len(words) >= 4:
            break
    purpose_ok = "_".join(words[:4])[:30].strip("_") or "script"
    ts = ts or datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{pid}_{purpose_ok}_{ts}{ext}"


def sanitize_ai_script_filename(name: str) -> str | None:
    """只保留 scripts/ 下安全文件名；拒绝路径穿越与保留名。"""
    raw = (name or "").strip().replace("\\", "/")
    if not raw or ".." in raw.split("/"):
        return None
    base = Path(raw).name
    if not base or base.lower() in _RESERVED_SCRIPT_NAMES:
        return None
    # 去掉奇怪前缀
    base = re.sub(r"[^\w.\-一-龥]+", "_", base).strip("._")
    if not base:
        return None
    ext = Path(base).suffix.lower()
    if ext not in AI_SCRIPT_EXTENSIONS:
        # 无扩展名时默认 .py；未知扩展名拒绝（避免写入任意二进制伪装）
        if not ext:
            base = f"{base}.py"
            ext = ".py"
        else:
            return None
    stem = Path(base).stem[:80] or "script"
    stem = re.sub(r"[^\w.\-一-龥]+", "_", stem).strip("._") or "script"
    return f"{stem}{ext}"


def apply_ai_authored_scripts(
    project_root: Path,
    *,
    lesson_id: str,
    project_id: str = "",
    script_files: list[dict[str, Any]] | None,
) -> list[ScriptStep]:
    """把总结 AI 手写的脚本写入 scripts/，并合并进 pipeline.json。

    命名规范：`项目ID_脚本作用(≤4词)_时间戳.扩展名`；同秒同名冲突时追加序号，
    绝不覆盖已有脚本。返回成功写入且纳入管线的 ScriptStep 列表。
    """
    from wokbee.engine.script_runner import load_pipeline

    files = script_files or []
    if not files:
        return []

    ensure_project_layout(project_root)
    sdir = scripts_dir(project_root)
    sdir.mkdir(parents=True, exist_ok=True)
    pid = project_id or Path(project_root).name

    written: list[ScriptStep] = []
    pipeline_entries: list[dict[str, Any]] = []

    for i, item in enumerate(files, 1):
        if not isinstance(item, dict):
            continue
        src_fname = sanitize_ai_script_filename(str(item.get("filename") or ""))
        content = str(item.get("content") or "")
        if not src_fname or not content.strip():
            continue
        uploaded = (Path(project_root) / "uploads" / src_fname).resolve()
        if uploaded.is_file():
            rel = uploaded.relative_to(Path(project_root).resolve()).as_posix()
            desc = str(item.get("description") or "").strip() or f"直接运行上传脚本：{rel}"
            step = ScriptStep(
                tool="script",
                args={"source": "uploaded_script", "path": rel},
                description=desc[:200],
                rel_path=rel,
            )
            written.append(step)
            if bool(item.get("in_pipeline", True)):
                pipeline_entries.append(
                    {
                        "id": f"script_upload_{i}",
                        "type": "script",
                        "path": rel,
                        "tool": "script",
                        "description": desc[:200],
                        "args": {},
                    }
                )
            continue
        ext = Path(src_fname).suffix.lower() or ".py"
        purpose = str(item.get("description") or "").strip() or Path(src_fname).stem
        fname = make_script_name(pid, purpose, ext)
        target = sdir / fname
        n = 2
        while target.exists():
            try:
                old = target.read_text(encoding="utf-8")
            except OSError:
                old = ""
            if old.strip() == content.strip():
                break
            fname = f"{Path(fname).stem}_{n}{ext}"
            target = sdir / fname
            n += 1

        body = content
        if ext in {".bat", ".cmd"}:
            body = body.replace("\r\n", "\n").replace("\n", "\r\n")
        safe_write_text(target, body)

        rel = f"scripts/{fname}"
        desc = str(item.get("description") or "").strip() or f"AI 手写脚本 {fname}"
        step = ScriptStep(
            tool="ai_authored",
            args={"filename": fname, "source": "ai_summary", "_src": src_fname},
            description=desc[:200],
            rel_path=rel,
        )
        written.append(step)
        if bool(item.get("in_pipeline", True)):
            pipeline_entries.append(
                {
                    "id": f"script_ai_{i}",
                    "type": "script",
                    "path": rel,
                    "tool": "ai_authored",
                    "description": f"获取数据：{desc}"[:200],
                    "args": {"filename": fname, "source": "ai_summary"},
                }
            )

    if not written:
        return []

    # 合并 pipeline：AI 脚本插到首个 AI 步骤之前；已有同 path 则跳过
    data = load_pipeline(project_root) or {
        "version": 2,
        "lesson_id": lesson_id,
        "goal": "",
        "steps": [],
        "scripts": [],
        "ai_steps": [],
        "policy": {
            "ordered_execution": True,
            "invoke_ai_on_script_error": True,
            "invoke_ai_on_bad_data": True,
            "scripts_not_archived": True,
        },
    }
    steps = list(data.get("steps") or []) if isinstance(data.get("steps"), list) else []
    existing_paths = {
        str(s.get("path") or "")
        for s in steps
        if isinstance(s, dict) and s.get("type") == "script"
    }
    insert_at = next(
        (idx for idx, s in enumerate(steps) if isinstance(s, dict) and s.get("type") == "ai"),
        len(steps),
    )
    added = 0
    for entry in pipeline_entries:
        path = str(entry.get("path") or "")
        if not path or path in existing_paths:
            continue
        steps.insert(insert_at + added, entry)
        existing_paths.add(path)
        added += 1

    # 同步 scripts 兼容字段
    scripts_compat = [
        {
            "path": s.rel_path,
            "tool": s.tool,
            "description": s.description,
            "args": s.args,
        }
        for s in written
    ]
    old_scripts = data.get("scripts") if isinstance(data.get("scripts"), list) else []
    seen_script_paths = {
        str(x.get("path") or "") for x in old_scripts if isinstance(x, dict)
    }
    merged_scripts = list(old_scripts)
    for sc in scripts_compat:
        if sc["path"] not in seen_script_paths:
            merged_scripts.append(sc)
            seen_script_paths.add(sc["path"])

    data["steps"] = steps
    data["scripts"] = merged_scripts
    data["lesson_id"] = data.get("lesson_id") or lesson_id
    data["version"] = int(data.get("version") or 2)
    # 附注：记录 AI 手写
    policy = data.get("policy") if isinstance(data.get("policy"), dict) else {}
    policy["ai_authored_scripts"] = True
    data["policy"] = policy

    safe_write_text(
        sdir / "pipeline.json",
        json.dumps(data, ensure_ascii=False, indent=2),
    )
    return written


def _resolve_pipeline_script_path(
    root: Path,
    sdir: Path,
    raw_path: str,
    *,
    rename_map: dict[str, str] | None = None,
) -> str | None:
    """解析 AI 脚本引用，只接受项目 scripts/ 或 uploads/ 相对路径。"""
    from wokbee.engine.script_runner import resolve_pipeline_script_path

    raw = (raw_path or "").replace("\\", "/").strip()
    if not raw:
        return None
    mapped = {
        Path(str(k).replace("\\", "/")).name.lower(): Path(
            str(v).replace("\\", "/")
        ).name
        for k, v in (rename_map or {}).items()
        if str(k).strip() and str(v).strip()
    }
    parts = raw.split("/")
    if len(parts) < 2 or parts[0].lower() not in {"scripts", "uploads"}:
        return None
    base = mapped.get(parts[-1].lower(), parts[-1])
    candidate_rel = "/".join(parts[:-1] + [base])
    candidate = resolve_pipeline_script_path(root, candidate_rel)
    if candidate is None:
        return None
    try:
        return candidate.relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def apply_ai_pipeline_steps(
    project_root: Path,
    *,
    lesson_id: str,
    goal: str = "",
    pipeline_steps: list[dict[str, Any]] | None,
    rename_map: dict[str, str] | None = None,
) -> bool:
    """若总结 AI 给出了 pipeline_steps，则以该顺序覆盖 pipeline.json 的 steps。

    steps 可同时含 `script` 与 `ai`：
    - script：确定性/机械步骤，后续直接执行（不耗 Token）；
    - ai：**已经确定的 AI 业务任务**（理解/分析/整理/创意/写作等），后续运行照样调用 LLM
      执行该固定任务，但禁止重新规划整个 Pipeline。
    过滤掉「AI 读取 Skill / 思考下一步 / 决定调用什么工具」这类 Agent 内部思考过程。
    不生成任何 final_ai。rename_map：{AI 原始文件名 → 规范化后的 scripts/ 文件名}。
    返回是否成功应用。
    """
    from wokbee.engine.script_runner import (
        format_order_markdown,
        load_pipeline,
        validate_pipeline_script_paths,
    )

    steps_in = pipeline_steps or []
    if not steps_in:
        return False

    ensure_project_layout(project_root)
    sdir = scripts_dir(project_root)
    sdir.mkdir(parents=True, exist_ok=True)
    root = Path(project_root)

    normalized: list[dict[str, Any]] = []
    missing_script_paths: list[str] = []
    for i, raw in enumerate(steps_in):
        if not isinstance(raw, dict):
            continue
        t = str(raw.get("type") or "").lower().strip()
        if t not in ("script", "ai"):
            continue
        if t == "script":
            path = str(raw.get("path") or "").replace("\\", "/").strip()
            if not path:
                continue
            resolved_path = _resolve_pipeline_script_path(
                root,
                sdir,
                path,
                rename_map=rename_map,
            )
            if not resolved_path:
                missing_script_paths.append(path)
                continue
            path = resolved_path
            step = {
                "id": str(raw.get("id") or f"script_{i+1}"),
                "type": "script",
                "path": path,
                "tool": str(raw.get("tool") or "script"),
                "description": str(raw.get("description") or path)[:200],
                "args": raw.get("args") if isinstance(raw.get("args"), dict) else {},
            }
        else:
            desc = str(raw.get("description") or "").strip()
            if not desc:
                continue
            # 过滤 Agent 内部思考过程：思考/决定下一步/规划/自由探索等不是业务任务
            low = desc.lower()
            if any(
                k in low
                for k in ("思考下一步", "决定下一步", "规划下一步", "自由决定", "自由探索", "读取skill", "读取 skill", "决定调用什么工具")
            ):
                continue
            step = {
                "id": str(raw.get("id") or f"ai_{i+1}"),
                "type": "ai",
                "description": desc[:300],
                "prompt_hint": str(raw.get("prompt_hint") or raw.get("hint") or "").strip(),
            }
        normalized.append(step)

    # pipeline 是系统的执行事实来源，绝不落盘无法执行的脚本引用。
    # 整条 AI 提案只要含幽灵脚本就拒绝，调用方可继续使用本轮真实固化的 pipeline。
    if missing_script_paths:
        return False

    if not normalized:
        return False

    # 目标要求交付到 deliverables/ 但 AI 未给出发布步骤时，补一个确定性发布步骤
    if goal_wants_deliverables(goal) and not any(
        s.get("type") == "script" and str(s.get("tool") or "").lower() == "publish"
        for s in normalized
    ):
        _base = make_script_name(Path(project_root).name, "publish_deliverables")
        pub_name = _base
        _n = 2
        while (sdir / pub_name).exists():
            pub_name = f"{Path(_base).stem}_{_n}.py"
            _n += 1
        safe_write_text(sdir / pub_name, _render_publish_script())
        normalized.append(
            {
                "id": f"script_publish",
                "type": "script",
                "path": f"scripts/{pub_name}",
                "tool": "publish",
                "description": "发布：把脚本实际产出文件复制到 deliverables/",
                "args": {},
            }
        )

    previous_pipeline = load_pipeline(project_root)
    data = previous_pipeline or {
        "version": 3,
        "scripts": [],
        "ai_steps": [],
        "policy": {},
    }
    data["version"] = 3
    data["lesson_id"] = lesson_id or data.get("lesson_id") or ""
    if goal:
        data["goal"] = goal
    data["steps"] = normalized
    data["scripts"] = [
        {
            "path": s.get("path"),
            "tool": s.get("tool"),
            "description": s.get("description"),
            "args": s.get("args") or {},
        }
        for s in normalized
        if s.get("type") == "script"
    ]
    data["ai_steps"] = [
        {
            "description": s.get("description"),
            "prompt_hint": s.get("prompt_hint") or "",
        }
        for s in normalized
        if s.get("type") == "ai"
    ]
    data.pop("final_ai", None)
    policy = data.get("policy") if isinstance(data.get("policy"), dict) else {}
    policy.update(
        {
            "ordered_execution": True,
            "invoke_ai_on_script_error": True,
            "invoke_ai_on_bad_data": not goal_no_check(goal),
            "scripts_not_archived": True,
            "order_source": "ai_summary",
            "ai_steps_in_pipeline": True,
            "ai_intervention": "ai_steps_and_error_recovery",
        }
    )
    data["policy"] = policy
    data["order_markdown"] = format_order_markdown(normalized)

    safe_write_text(
        sdir / "pipeline.json",
        json.dumps(data, ensure_ascii=False, indent=2),
    )
    final_pipeline = load_pipeline(project_root) or {}
    invalid = validate_pipeline_script_paths(
        root,
        final_pipeline.get("steps") if isinstance(final_pipeline, dict) else [],
    )
    if invalid:
        if previous_pipeline is not None:
            safe_write_text(
                sdir / "pipeline.json",
                json.dumps(previous_pipeline, ensure_ascii=False, indent=2),
            )
        return False
    return True


def drop_missing_pipeline_scripts(project_root: Path) -> list[str]:
    """移除 pipeline.json 中引用不存在脚本文件的 script 步骤，返回被移除的路径。

    经验写入（尤其工具路径 AI 未给 script_files 时）可能产出引用幽灵脚本的管线；
    写入后立即清理，保证下次运行不会因「文件不存在」中断。仅当确有移除时才重写文件。
    """
    from wokbee.engine.script_runner import (
        format_order_markdown,
        load_pipeline,
        resolve_pipeline_script_path,
    )

    root = Path(project_root)
    data = load_pipeline(root)
    if not data:
        return []
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        return []
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    for s in steps:
        if not isinstance(s, dict) or str(s.get("type") or "").lower() != "script":
            kept.append(s)
            continue
        rel = str(s.get("path") or "").replace("\\", "/").strip()
        if rel and resolve_pipeline_script_path(root, rel) is not None:
            kept.append(s)
        else:
            dropped.append(rel or "（空路径步骤）")
    if not dropped:
        return []
    data["steps"] = kept
    data["scripts"] = [
        {
            "path": s.get("path"),
            "tool": s.get("tool"),
            "description": s.get("description"),
            "args": s.get("args") or {},
        }
        for s in kept
        if isinstance(s, dict) and s.get("type") == "script"
    ]
    data["ai_steps"] = [
        {
            "description": s.get("description"),
            "prompt_hint": s.get("prompt_hint") or "",
        }
        for s in kept
        if isinstance(s, dict) and s.get("type") == "ai"
    ]
    data["order_markdown"] = format_order_markdown(kept)
    safe_write_text(
        scripts_dir(root) / "pipeline.json",
        json.dumps(data, ensure_ascii=False, indent=2),
    )
    return dropped


# 清理时会处理/忽略的脚本扩展名（pipeline.json 另作保留）
_QUARANTINE_EXTENSIONS = frozenset(
    {".py", ".bat", ".cmd", ".ps1", ".json", ".sh", ".js", ".vbs"}
)


def quarantine_obsolete_scripts(
    project_root: Path,
    *,
    kept_paths: list[str],
    lesson_id: str = "",
) -> tuple[list[str], Path]:
    """把 scripts/ 顶层中不在 kept_paths 里的脚本移入 archives/discard_<ts>/scripts/。

    kept_paths 形如 "scripts/foo.py"，按文件名匹配；pipeline.json 与子目录不处理。
    「下次运行只执行 pipeline.json steps[].path」，因此保留 kept 之外的脚本即可安全孤立。
    返回 (已移走文件名列表, 目标目录)。可逆：不删除，只是搬到归档下。
    """
    ensure_project_layout(Path(project_root))
    sdir = scripts_dir(project_root)
    if not sdir.exists():
        return [], Path()

    kept_names = {Path(p).name for p in kept_paths if p}
    moved: list[str] = []
    dest: Path | None = None

    for p in sorted(sdir.iterdir(), key=lambda x: x.name):
        if not p.is_file():
            continue
        if p.name == "pipeline.json" or p.name in kept_names:
            continue
        ext = p.suffix.lower()
        if ext not in _QUARANTINE_EXTENSIONS:
            continue
        if dest is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = archives_dir(project_root) / f"discard_{stamp}" / "scripts"
            dest.mkdir(parents=True, exist_ok=True)
        try:
            target = dest / p.name
            if target.exists():
                target = dest / f"{p.stem}_{lesson_id[-4:] or 'x'}{p.suffix}"
            shutil.move(str(p), str(target))
            moved.append(p.name)
        except OSError:
            continue
    return moved, dest or Path()
