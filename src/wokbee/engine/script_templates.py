"""脚本模板：固化的网络/命令脚本头部与 callback 保存函数。"""

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
