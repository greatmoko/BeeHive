"""从运行轨迹固化可本地执行的脚本（不耗 Token）。"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from tokbee.core.safe_io import safe_write_text

from wokbee.core.paths import archives_dir, ensure_project_layout, scripts_dir
from wokbee.engine.script_events import (
    SCRIPTABLE_TOOLS,
    SolidifyResult,
    ScriptStep,
    _execute_script_label,
    _normalize_execute_command,
    _project_script_path_from_command,
    _script_execute,
    _script_tokens,
    extract_scriptable_from_events,
    extract_scriptable_from_path_text,
)
from wokbee.engine.script_templates import _COMMON_HEADER, _EXECUTE_HEADER, _hdr
from wokbee.engine.script_solidification import solidify_scripts
from wokbee.engine.script_ai import apply_ai_authored_scripts
from wokbee.engine.script_pipeline_writer import (
    apply_ai_pipeline_steps,
    drop_missing_pipeline_scripts,
    quarantine_obsolete_scripts,
)

# 目标中明确要求把产物放进 deliverables/ 的措辞（决定是否自动补一个确定性发布步骤）
_DELIVERABLE_HINTS = ("deliverables", "交付")
# 目标中明确「不校验 / 不检查 / 不验证」产出内容的措辞（policy.invoke_ai_on_bad_data=False）
_NO_CHECK_HINTS = (
    "不校验", "不要校验", "无需校验", "不检查", "不要检查", "无需检查",
    "不验证", "不要验证", "无需验证", "不审查", "不要审查", "不审阅", "不要审阅",
    "不需要检查", "不需要校验", "不需要验证",
)

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
