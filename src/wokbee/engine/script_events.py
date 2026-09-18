"""从运行事件中提取可固化脚本步骤。"""

from __future__ import annotations

import ast
import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wokbee.engine.script_templates import _EXECUTE_HEADER, _hdr

SCRIPTABLE_TOOLS = frozenset({"web_search", "http_get", "http_request", "execute"})
_SCRIPT_EXTS = (".py", ".bat", ".cmd", ".ps1")
_REDIRECT_RE = re.compile(
    r"""(?ix)
    \s*(?:>>?)\s*(?:"[^"]*"|'[^']*'|[^\s&|;]+)
    (?:\s*2>&1|\s*2>\s*(?:"[^"]*"|'[^']*'|[^\s&|;]+))?
    |
    \s*2>&1
    """
)

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


