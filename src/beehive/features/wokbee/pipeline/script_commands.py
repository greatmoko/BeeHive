"""本地脚本命令识别、路径与跨机改写。"""
from __future__ import annotations
import re
import shlex
from pathlib import Path
_SCRIPT_EXTS = (".py", ".bat", ".cmd", ".ps1")

_REDIRECT_RE = re.compile(
    r"""(?ix)
    \s*(?:>>?)\s*(?:"[^"]*"|'[^']*'|[^\s&|;]+)
    (?:\s*2>&1|\s*2>\s*(?:"[^"]*"|'[^']*'|[^\s&|;]+))?
    |
    \s*2>&1
    """
)

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
    """从命令中找当前项目下的 uploads/ 或 memory/ 脚本（兼容旧 scripts/）。"""
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
