"""经验总结：每次总结生成带时间戳的新文档；运行时只加载最新一份。

经验只记录：摘要 / 成功实现路径 / 注意事项。
不记录结果、产物或交付内容；运行环境由系统每次自动注入。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from tokbee.core.safe_io import safe_write_text

from wokbee.core.paths import ensure_project_layout, memory_dir, scripts_dir
from wokbee.engine.lesson_models import Lesson, _now, _slug, _stamp, render_lesson_md

EXPERIENCES_SUBDIR = "experiences"
LEGACY_SINGLE = "EXPERIENCE.md"
_EXP_NAME_RE = re.compile(r"^exp_(\d{8}_\d{6}(?:_\d{3})?)(?:_[a-z0-9]+)?\.md$", re.I)




def build_environment_block(
    *,
    model: str = "",
    policy: str = "",
    project_root: str = "",
    extra: str = "",
) -> str:
    """经验总结用运行环境块（与 Agent 会话上下文同源）。"""
    from wokbee.engine.runtime_env import build_runtime_env_block

    return build_runtime_env_block(
        project_root=project_root,
        model=model,
        policy=policy,
        extra=extra,
    )


def collect_scripts_context(project_root: Path, *, max_chars: int = 12000) -> str:
    """收集 scripts/pipeline.json 与脚本源码摘要，供 AI 总结。"""
    root = Path(project_root)
    sdir = scripts_dir(root)
    parts: list[str] = []
    pipe = sdir / "pipeline.json"
    if pipe.exists():
        try:
            parts.append(
                "### scripts/pipeline.json\n```json\n"
                + pipe.read_text(encoding="utf-8")[:4000]
                + "\n```"
            )
        except OSError:
            pass
    if sdir.exists():
        for p in sorted(sdir.glob("*.py"))[:20]:
            try:
                body = p.read_text(encoding="utf-8")
            except OSError:
                continue
            if len(body) > 2500:
                body = body[:2500] + "\n# …(截断)"
            parts.append(f"### scripts/{p.name}\n```python\n{body}\n```")
    text = "\n\n".join(parts) if parts else "（尚无 scripts/ 内容）"
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…(脚本上下文截断)"
    return text


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


_AI_SUMMARY_SYSTEM = """你是 WokBee 的「经验总结」助手。根据「上一份经验 + 本次运行日志 + 现有脚本」总结可复用的流程经验。
不要输出实现过程、压缩轨迹、关键线索或问题与解决方案章节。

⚠️ 首要提醒（最重要）：你总结的**经验文档 / pipeline.json 步骤 / 脚本**，在后续项目运行时会被**严格照章执行**——
错误、含糊、不严谨的路径（脚本地址、命令、数据源）将直接导致后续运行失败，代价远高于本次修正。因此必须：
- 只总结**真实验证过、有效、可复用**的内容，且**尽量简短**；剔除一切失败、试错、被弃用或重复的尝试。
- 每条路径、脚本名、命令都必须来自本次运行日志，**禁止编造**；脚本路径须与项目下真实文件一致。
- 用户已上传到 uploads/ 的脚本必须直接运行原文件；禁止 Copy-Item 到项目根目录，禁止再次包装已有 scripts/ 脚本。
- 一律使用虚拟路径（scripts/、workspace/、deliverables/、uploads/、memory/…），**禁止 Windows 绝对路径**（如 C:\\Users\\…）。

硬性要求：
1. 经验只包含：**摘要**、**成功实现路径**、**注意事项**；其中成功实现路径必须与 pipeline.json 对齐。
2. 禁止写入：最终结果数值、交付产物内容、报告正文、截图描述、成功产出的具体文案；不记录运行环境（系统每次自动注入）。
3. 不要引用或依赖 archives/ 归档数据。
4. **success_path（成功实现路径）必须与 pipeline_steps 一一对应**：
   - pipeline_steps 是机器执行顺序的唯一事实来源；success_path 只能解释这些步骤，不能另行增加目录查看、文件搜索、验证或 Agent 内部思考步骤。
   - 最终落盘时系统会再次从实际写入的 pipeline 生成 success_path，因此不要编造 pipeline 之外的路径。
   - 在 pipeline_steps 尚未确定前，只保留真正成功且必要的有序步骤，步骤尽量精简：
   - 执行顺序直接体现在编号中（脚本步骤 ↔ AI 环节按真实顺序排），不再单列「执行顺序/可本地脚本步骤/需 AI 完成的步骤」章节。
   - 若后续步骤依赖前置结果（需要前置数据/确认才能选对输入），应按**逻辑依赖**顺序排，勿把历史里「先取数、后补前置确认」的脏顺序原样固化。例如「先用 Get-Date 确认当前日期，再选取对应日期的数据」「先读配置，再跑脚本」。
    - 剔除所有失败调用、试错、被弃用/未采用的方案；保留每个真实业务步骤的边界，
      不强制合并相邻的同类型步骤，因为同一脚本的重复执行可能是有意的验证步骤。
   - 每步必须按**固定格式**书写：
     `序号. 执行角色: "{执行内容}"; 【步骤说明】`
     - **序号**：从 1 开始递增。
     - **执行角色**：AI / 工具调用 / 脚本执行 / 系统执行 等——AI 判断加工标 `AI`，文件/联网等工具标 `工具调用`，本地脚本步骤标 `脚本执行`（自动执行，AI 不介入），系统自动过程标 `系统执行`。
     - **执行内容**：用 `{}` 包起来，写明**详细且明确**的执行命令 / 提示词 / 脚本名称与路径（脚本步骤可直接引用 `uploads/` 用户脚本，或引用 `scripts/` 下真实脚本）。
     - **步骤说明**：用 `【】` 包起来，是对执行内容的解释性描述（做什么 × 达成什么目的）。
     - 示例：
       `1. 工具调用: "{cmd: 读取 workspace/script_callback_*.md}"; 【查看上一步脚本回调，确认产物完整】`
       `2. 脚本执行: "{cmd: execute scripts/query_weather.bat}"; 【运行天气查询脚本（自动执行），原始数据落 workspace/】`
       `3. AI: "{提示词: 依据 callback 数据提炼要点并成文，写入 deliverables/}"; 【AI 环节：成文交付】`
5. 自动化脚本与管线约定（重要）：
   - 可复用本地命令落到项目 `scripts/`；运行输出落到 `workspace/script_callback_*.md`。
   - **只在本次运行日志中确实出现过、且尚未固化的可复用命令才写 script_files**（.py/.bat/.cmd/.ps1/.json/.sh/.js/.vbs）。
      禁止凭空发明「预检/校验/回读」等日志里没有的脚本；同一脚本只有在真实路径确需
      重复验证时才重复写入步骤。
    - **pipeline_steps 必须基于本次运行日志中实际执行过的步骤，按真实时间顺序逐步列出**；每一步判定类型：
     - `script`：能够确定性、机械、重复执行的工作（API 请求、文件处理、数据转换、发布复制等）→
       用户上传脚本直接引用 `uploads/...`，其他新脚本才固化为 `scripts/...`，后续直接执行、不耗 Token；
     - `ai`：必须依赖 AI 的理解/分析/整理/创意/写作/判断的工作（如“根据收集的材料撰写报告”）→
       固化为**明确的业务任务**（description + prompt_hint），后续运行仍调用 LLM 执行该固定任务，
       但**禁止重新规划整个 Pipeline**；
     **禁止保存 Agent 内部思考过程**（读取 Skill、思考下一步、决定调用什么工具、自由探索等）——
     那不是业务任务；ai 步骤必须是一句明确的业务任务，例如“根据前面收集的数据生成报告”。
     **禁止凭空新增日志中没有的步骤/脚本**；**禁止根据 AI 自己的理解增加目标里没有的任务**
     （如目标只要求「运行脚本并把产物放到 deliverables」，就不要再加检查/总结/校验步骤）。
     简单任务（如：执行用户脚本 → 发布产物到 deliverables）通常只需 2~3 步；
     复杂任务按实际执行可以有更多步骤。
   - **交付约定**：若目标要求把产物放到 deliverables/，pipeline_steps 必须包含一个
     「发布」脚本步骤——把脚本**实际产出文件**复制/移动到 deliverables/（保留原始文件与
     文件名，**不要合并成 final.md 代替原始产物**）；脚本步骤 path 指向项目内真实文件。
    - **pipeline_steps** 决定下次「运行」的真实顺序：按数组从头到尾逐步执行——script 步骤自动跑
     （不耗 Token）；ai 步骤调用 LLM 执行已确定的业务任务（按需消耗 Token）。
       **所有 script path 必须是项目相对虚拟路径**（仅允许 `scripts/...` 或 `uploads/...`，禁止 Windows 绝对路径）；提交前先用文件工具确认文件存在且可访问，写入后系统会重新读取 pipeline 并用同一规则复核，失败则拒绝保存。
       **不生成 final_ai**，也不在管线结尾强制再唤一次 AI；如果“总结/写报告/生成内容”本身就是
     用户 Goal 的一部分，它应作为正常的 `ai` 步骤固化在管线中。
6. **注意事项（notes）写作规范**：
   - 采用**无序列表**（每项以 `-` 开头），不要按「问题1/处理1」编号排序。
   - 每条 = **问题加粗** + 解决办法（含具体规避做法或正确写法），同一条目内给出。
   - 格式：`- **问题简述**：解决办法（具体做法）。`
   - 示例：
     - **查询文件不存在**：以虚拟路径访问与校验（workspace/、scripts/…），勿使用 Windows 绝对路径。
     - **脚本 callback 缺失**：脚本执行后把输出写入 workspace/script_callback_*.md，AI 环节先读再写，禁止编造。
7. 用中文。输出必须是一个 JSON 对象（不要 Markdown 围栏），字段如下：
{
  "summary": "摘要：概要介绍经验的主要作用（一两段，非结果）",
  "success_path": "仅成功且必要的有序步骤（按固定格式：序号. 执行角色: \"{执行内容}\"; 【步骤说明】，每步=操作+目的，执行顺序直接体现在编号中）",
  "notes": "注意事项：无序列表（- 开头），每条 = **加粗问题** + 解决办法（具体做法）",
  "used_skills": ["skill-folder-name"],
  "reference_materials": [
    {"path": "uploads/references/config.json", "note": "服务端环境参数，复跑需用"}
  ],
  "script_files": [
    {"filename": "query_weather.bat", "content": "@echo off\\n...", "description": "...", "in_pipeline": true}
  ],
  "pipeline_steps": [
    {"type": "script", "path": "scripts/collect_data.py", "description": "API 请求并保存原始数据到 workspace/（自动执行）"},
    {"type": "ai", "description": "根据 workspace/ 中的收集材料整理分析并撰写报告", "prompt_hint": "先读 workspace/script_callback_*.md，再写报告到 deliverables/"},
    {"type": "script", "path": "scripts/publish_deliverables.py", "description": "发布：把脚本实际产出文件复制到 deliverables/（保留原始文件）"}
  ]
}
说明：
- **script 步骤**：确定性/机械工作，后续自动执行（不耗 Token）；
  **ai 步骤**：明确的业务任务（理解/分析/整理/创意/写作/判断），后续运行照样调 LLM 执行该固定任务，
  但**禁止重新规划整个 Pipeline**。不要把「读取 Skill / 思考下一步 / 决定调用什么工具」保存为
  ai 步骤；如果“总结/写报告/生成内容”本身就是用户 Goal 的一部分，它应作为正常的 `ai` 步骤固化，
  而不是结尾的 final_ai。不生成 final_ai，也不在管线结尾强制再唤一次 AI。
- **严格对齐目标与实际执行**：不要根据 AI 自己的理解增加目标里没有的任务（如检查、总结、校验）；
  目标要求什么就交付什么。若目标要求交付文件到 deliverables/，必须含一个把**实际产出文件**
  复制到 deliverables/ 的发布步骤（不得用 final.md 合并代替原始文件）。
- **script_files**：脚本最终会按规范重命名为 `项目ID_脚本作用(≤4词)_时间戳.扩展名`（filename 仅作作用提示）；
  内容必须完整可独立运行，description 写清作用。**绝不覆盖/删除 scripts/ 下已有脚本**。
- **used_skills**：本次真实调用过的全局 Skill 目录名（如 "web-search"、"pdf-tools"），供快照到 uploads/references/skills/。
- **reference_materials**：本次用到的可复用外部材料（第三方代码/登录与密钥配置/环境参数等），需保存进 uploads/references/ 并登记；敏感信息仅供本机使用。没有则为空数组。
- 有可复用命令时尽量同时给出 script_files 与 pipeline_steps；没有则可为空数组。
"""


def _extract_json_object(text: str) -> str | None:
    """从可能带围栏/前后叙述的文本里截出第一个平衡的 {…} JSON 对象。"""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def build_success_path_from_pipeline(steps: list[dict] | None) -> str:
    """把最终 pipeline 渲染成人和 AI 可读的成功路径。

    pipeline 是执行顺序的唯一事实来源；经验只负责解释同一组步骤，不能再独立
    从运行轨迹推导另一套顺序。
    """
    lines: list[str] = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        kind = str(step.get("type") or "").lower().strip()
        description = " ".join(str(step.get("description") or "").split())[:300]
        if kind == "script":
            path = str(step.get("path") or "").replace("\\", "/").strip()
            if not path:
                continue
            tool = str(step.get("tool") or "script").strip()
            action = f"脚本: {path}"
            if tool and tool != "script":
                action = f"脚本: {path}; 工具: {tool}"
            lines.append(
                f'{len(lines) + 1}. 脚本执行: "{{{action}}}"; '
                f"【{description or '执行确定性脚本步骤'}】"
            )
        elif kind == "ai":
            hint = " ".join(str(step.get("prompt_hint") or "").split())[:300]
            action = f"业务任务: {description or '执行已确定的 AI 业务任务'}"
            if hint:
                action += f"; 提示: {hint}"
            lines.append(
                f'{len(lines) + 1}. AI: "{{{action}}}"; '
                f"【{description or '执行已确定的 AI 业务任务'}】"
            )
    return "\n".join(lines)


def sync_lesson_from_pipeline(lesson: Lesson, project_root: Path) -> bool:
    """从最终 pipeline 同步经验的执行主线和脚本清单。"""
    from wokbee.engine.script_runner import load_pipeline

    data = load_pipeline(Path(project_root))
    steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(steps, list):
        return False
    lesson.success_path = build_success_path_from_pipeline(steps)
    lesson.scripts = [
        str(step.get("path") or "").replace("\\", "/").strip()
        for step in steps
        if isinstance(step, dict)
        and str(step.get("type") or "").lower() == "script"
        and str(step.get("path") or "").strip()
    ]
    lesson.pipeline = "scripts/pipeline.json"
    return True


def _parse_ai_summary_json(text: str) -> dict | None:
    """宽松解析 AI 总结 JSON：去围栏 → 直接 → 截首个平衡 {…} → 失败返回 None。"""
    t = (text or "").strip()
    if not t:
        return None
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    try:
        data = json.loads(t)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    obj = _extract_json_object(t)
    if obj is not None:
        try:
            data = json.loads(obj)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def summarize_lesson_with_ai(
    *,
    model: Any,
    goal: str,
    outcome: str,
    previous_experience: str,
    run_log: str,
    scripts_context: str,
    environment_hint: str = "",
    phase_states: str = "",
) -> dict[str, Any]:
    """调用模型总结经验；失败时抛出异常由调用方回退。

    返回字段均为 str，另含：
    - script_files: list[dict]（AI 手写脚本，可为空）
    - pipeline_steps: list[dict]（有序管线步骤）
    - used_skills: list[str]（本次用到的 Skill 目录名）
    - reference_materials: list[dict]（需保存进 uploads/references/ 的材料）
    生成过程在内部收齐，结束后一次性返回，避免向时间线刷进度气泡。
    """
    user = (
        f"项目目标：{goal or '（未设置）'}\n"
        f"本轮 outcome：{outcome}\n\n"
        f"## 上一份经验（可能为空）\n{previous_experience or '（无）'}\n\n"
        f"## 本次运行日志\n{run_log}\n\n"
        f"## 本轮阶段状态（按时间顺序；失败时必须据此修正管线）\n"
        f"{phase_states or '（没有预定义 pipeline 阶段；请从运行日志还原真实步骤）'}\n\n"
        f"## 现有脚本与 pipeline\n{scripts_context}\n\n"
        f"## 环境提示\n{environment_hint or '（无）'}\n\n"
        "请输出符合要求的 JSON。你总结的经验/管线/脚本在后续运行时会被**严格照章执行**，"
        "错误或不严谨的路径会直接破坏后续工作：务必只写真实验证过、可复用且**尽量短**的步骤；"
        "路径一律用虚拟路径（scripts/、workspace/、deliverables/、uploads/、memory/…），禁止 Windows 绝对路径。"
        "success_path 只保留**成功且必要**的有序步骤，每步按固定格式："
        "`序号. 执行角色:\"{执行内容}\";[步骤说明]`（执行角色=AI/工具调用/脚本执行/系统执行；"
        "`{}` 内写详细命令/脚本地址/提示词，`[]` 内写解释性说明），执行顺序直接体现在编号中，"
        "剔除失败/试错/被弃用尝试，但不要强制合并相邻的同类型步骤；"
        "notes 用无序列表，每条=**加粗问题**+解决办法。"
        "若日志里出现可复用"
        "的本地脚本/命令（如 execute 跑 .py/.bat、Skill 脚本），请在 script_files 写出完整源码，"
        "并在 pipeline_steps 明确给出每次执行的脚本地址/命令。"
        "pipeline_steps 以日志中真实跑过的步骤为准，严格保持实际顺序和步骤边界，禁止凭空新增；每步判定类型："
        "`script`=确定性/机械工作（API 请求、文件处理、数据转换、发布复制等，后续自动执行不耗 Token）；"
        "`ai`=必须依赖 AI 的理解/分析/整理/创意/写作/判断的业务任务（如“根据收集的材料撰写报告”，"
        "后续运行照样调 LLM 执行该固定任务，但**禁止重新规划整个 Pipeline**）。"
        "**禁止把 Agent 内部思考过程保存为 ai 步骤**（读取 Skill、思考下一步、决定调用什么工具、"
        "自由探索等）；ai 步骤必须是一句明确的业务任务。"
        "**严格对齐项目目标与实际执行**：不要根据 AI 自己的理解增加目标里没有的任务"
        "（如目标只要求运行脚本并把产物放到 deliverables，就不要再加检查/总结/校验步骤）。"
        "不生成 final_ai，也不在管线结尾强制再唤一次 AI；如果“总结/写报告/生成内容”本身就是"
        "用户 Goal 的一部分，它应作为正常的 ai 步骤固化在管线中。"
        "若目标要求交付文件到 deliverables/，pipeline_steps 必须包含一个发布步骤："
        "把脚本**实际产出文件**复制/移动到 deliverables/（保留原始文件，不要合并成 final.md 代替产物）。"
        "简单任务（执行→发布）通常 2~3 步。"
        "请逐一判断阶段状态：成功、失败-AI接管后成功、失败-AI接管后失败。"
        "只要存在失败阶段，就必须直接产出修正后的 pipeline_steps/脚本方案，"
        "让下一轮按修正版执行并更新项目经验；不要只描述失败而不修正。"
        "若用到了第三方代码/登录/环境参数，"
        "请填到 used_skills 与 reference_materials，供保存到 uploads/references/ 供下次稳定复跑。"
    )
    messages = [
        {"role": "system", "content": _AI_SUMMARY_SYSTEM},
        {"role": "user", "content": user},
    ]
    text = ""
    # 优先 stream 仅用于内部拼装；不向外刷进度。失败则 invoke。
    try:
        parts: list[str] = []
        for chunk in model.stream(messages):
            piece = getattr(chunk, "content", None)
            if piece is None:
                continue
            if isinstance(piece, list):
                piece = "".join(
                    b.get("text", "") if isinstance(b, dict) else str(b) for b in piece
                )
            piece = str(piece)
            if piece:
                parts.append(piece)
        text = "".join(parts).strip()
    except Exception:
        text = ""
    if not text:
        resp = model.invoke(messages)
        raw = getattr(resp, "content", None) or str(resp)
        if isinstance(raw, list):
            raw = "\n".join(
                b.get("text", "") if isinstance(b, dict) else str(b) for b in raw
            )
        text = str(raw).strip()

    data = _parse_ai_summary_json(text)
    if data is None:
        raise ValueError("AI 总结未返回有效 JSON（已按宽松解析尝试，仍失败）")
    out: dict[str, Any] = {}
    for key in (
        "summary",
        "success_path",
        "notes",
    ):
        val = data.get(key)
        out[key] = str(val).strip() if val is not None else ""
    out["script_files"] = _normalize_ai_script_files(data.get("script_files"))
    out["pipeline_steps"] = _normalize_ai_pipeline_steps(data.get("pipeline_steps"))
    out["used_skills"] = _normalize_ai_used_skills(data.get("used_skills"))
    out["reference_materials"] = _normalize_ai_reference_materials(
        data.get("reference_materials")
    )
    return out


_AI_SUMMARY_JUDGE_SYSTEM = """你是 WokBee 的「经验与 Pipeline 是否需要更新」决策助手。

背景：项目已有至少一份经验与 scripts/pipeline.json（后续运行按 steps 顺序执行）。你需要根据
「最新经验 + 本次运行日志 + 本轮结果」判断**是否值得**新建一份更新后的经验、并重写 pipeline.json。

本次运行若没有出现任何异常（脚本全部成功、输出正常），默认不更新。

需要判断的三种情形（结合本次异常及最终成功解决方案）：
1. **偶发错误**（网络抖动、临时超时、外部服务暂不可用等）：本次偶发，不修改 Pipeline，也不改经验。
2. **原 Pipeline 本身的问题**（脚本/顺序/命令有误或过时导致失败，AI 已用新方法修正）：需要更新
   经验并重写 Pipeline（以本次真实成功执行的操作/脚本/顺序为准）。
3. **发现了更稳定、更好的执行路径**（新脚本、更快的顺序、更可靠的命令，且已真实验证成功）：
   应该替换原 Pipeline 与经验。

不必更新的情形：
1. 完全按已有经验+脚本稳定复跑成功，无新错误、无新方法、执行顺序未变（含偶发错误且已由
   现有路径稳定恢复）。
2. 仅结果/数据变化（经验不记录结果），流程/方法/环境层面无新信息。
3. 运行被用户取消，无实质新信息。

硬性要求：
- 只返回一个 JSON 对象（不要 Markdown 围栏），格式：
  {"should_update": true 或 false, "reason": "一句话理由，中文"}
- should_update 默认应偏向 false（省 token，且避免每次都覆盖 Pipeline）；只有确有意义的新方法 /
  新错误修正 / 新顺序（即情形 2、3）时才为 true。
"""


def judge_should_update_experience(
    *,
    model: Any,
    goal: str,
    outcome: str,
    previous_experience: str,
    run_log: str,
) -> tuple[bool, str]:
    """用轻量模型调用判断本次运行是否值得更新经验。失败一律返回 (False, "")。"""
    user = (
        f"项目目标：{goal or '（未设置）'}\n"
        f"本轮 outcome：{outcome}\n\n"
        f"## 最新一份经验（仅流程方法，不含结果）\n{previous_experience or '（无）'}\n\n"
        f"## 本次运行日志\n{run_log or '（无）'}\n\n"
        "请判断是否需要更新经验，仅返回 JSON。"
    )
    messages = [
        {"role": "system", "content": _AI_SUMMARY_JUDGE_SYSTEM},
        {"role": "user", "content": user},
    ]
    text = ""
    try:
        parts: list[str] = []
        for chunk in model.stream(messages):
            piece = getattr(chunk, "content", None)
            if piece is None:
                continue
            if isinstance(piece, list):
                piece = "".join(
                    b.get("text", "") if isinstance(b, dict) else str(b) for b in piece
                )
            piece = str(piece)
            if piece:
                parts.append(piece)
        text = "".join(parts).strip()
    except Exception:
        text = ""
    if not text:
        try:
            resp = model.invoke(messages)
            raw = getattr(resp, "content", None) or str(resp)
            if isinstance(raw, list):
                raw = "\n".join(
                    b.get("text", "") if isinstance(b, dict) else str(b) for b in raw
                )
            text = str(raw).strip()
        except Exception:
            return False, ""
    data = _parse_ai_summary_json(text)
    if not isinstance(data, dict):
        return False, ""
    return bool(data.get("should_update")), str(data.get("reason") or "").strip()



def _normalize_ai_script_files(raw: Any) -> list[dict[str, Any]]:
    """校验并规整 AI 返回的 script_files。"""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        filename = str(
            item.get("filename") or item.get("name") or item.get("path") or ""
        ).strip()
        content = item.get("content")
        if content is None:
            content = item.get("source") or item.get("code") or ""
        content = str(content)
        if not filename or not content.strip():
            continue
        if len(content) > 256_000:
            content = content[:256_000]
        desc = str(item.get("description") or item.get("desc") or "").strip()
        in_pipeline = item.get("in_pipeline")
        if in_pipeline is None:
            in_pipeline = item.get("pipeline", True)
        out.append(
            {
                "filename": filename,
                "content": content,
                "description": desc,
                "in_pipeline": bool(in_pipeline),
            }
        )
    return out


def _normalize_ai_pipeline_steps(raw: Any) -> list[dict[str, Any]]:
    """规整 AI 给出的有序管线步骤。

    不做硬性数量限制（复杂任务步骤可以多、AI 环节可以多）：
    仅去掉**完全重复**的步骤（同一 type+path+description+args+prompt_hint），
    避免明显的重复浪费；同一脚本在不同阶段跑（参数/说明不同）会保留。
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw[:80]):
        if not isinstance(item, dict):
            continue
        t = str(item.get("type") or "").lower().strip()
        if t not in ("script", "ai"):
            continue
        step: dict[str, Any] = {
            "id": str(item.get("id") or f"{t}_{i+1}"),
            "type": t,
            "description": str(item.get("description") or "").strip()[:300],
        }
        if t == "script":
            path = str(item.get("path") or item.get("filename") or "").strip().replace("\\", "/")
            if path and not path.startswith("scripts/"):
                path = f"scripts/{Path(path).name}"
            if not path:
                continue
            step["path"] = path
            step["tool"] = str(item.get("tool") or "ai_authored")
            step["args"] = item.get("args") if isinstance(item.get("args"), dict) else {}
        else:
            step["prompt_hint"] = str(item.get("prompt_hint") or item.get("hint") or "").strip()
            if not step["description"]:
                step["description"] = "AI 步骤"
        out.append(step)
    # 不去重：同一脚本在不同步骤重复执行可能是经过验证的成功路径。
    return out


def _dedupe_identical_pipeline_steps(steps: list[dict]) -> list[dict]:
    """去掉完全重复的步骤（type+path+description+args+prompt_hint 全同）。"""
    if not steps:
        return steps
    seen: set[str] = set()
    out: list[dict] = []
    for s in steps:
        key = json.dumps(
            {
                "t": s.get("type"),
                "p": str(s.get("path") or ""),
                "d": str(s.get("description") or "").strip(),
                "a": s.get("args") if isinstance(s.get("args"), dict) else {},
                "h": str(s.get("prompt_hint") or "").strip(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _normalize_ai_used_skills(raw: Any) -> list[str]:
    """规整 AI 返回的 used_skills（Skill 目录名列表，去重保序）。"""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw[:50]:
        if isinstance(item, str):
            s = item.strip()
        elif isinstance(item, dict):
            s = str(item.get("name") or "").strip()
        else:
            s = ""
        if s and s not in out:
            out.append(s)
    return out


def _normalize_ai_reference_materials(raw: Any) -> list[dict[str, Any]]:
    """规整 AI 返回的 reference_materials（path + note）。"""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:50]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        note = str(item.get("note") or item.get("desc") or "").strip()[:300]
        if not path and not note:
            continue
        out.append({"path": path, "note": note})
    return out


class LessonStore:
    """`memory/experiences/exp_YYYYMMDD_HHMMSS.md` 多份经验；运行只读最新。"""

    def __init__(self, project_root: Path):
        self.root = Path(project_root)
        ensure_project_layout(self.root)
        self.memory = memory_dir(self.root)
        self.memory.mkdir(parents=True, exist_ok=True)
        self.experiences_dir = self.memory / EXPERIENCES_SUBDIR
        self.experiences_dir.mkdir(parents=True, exist_ok=True)
        self._maybe_migrate_legacy()

    @property
    def experience_path(self) -> Path | None:
        return self.latest_path()

    @property
    def index_path(self) -> Path:
        latest = self.latest_path()
        return latest if latest else self.experiences_dir

    def _maybe_migrate_legacy(self) -> None:
        legacy_single = self.memory / LEGACY_SINGLE
        if legacy_single.exists() and legacy_single.is_file():
            if not any(self.experiences_dir.glob("exp_*.md")):
                dest = self.experiences_dir / f"exp_{_stamp()}_migrated.md"
                try:
                    safe_write_text(dest, legacy_single.read_text(encoding="utf-8"))
                except OSError:
                    pass
            try:
                bak = self.memory / "EXPERIENCE.md.bak"
                if not bak.exists():
                    legacy_single.replace(bak)
            except OSError:
                pass

        old_idx = self.memory / "EXPERIENCES.md"
        if old_idx.exists() and not any(self.experiences_dir.glob("exp_*.md")):
            try:
                safe_write_text(
                    self.experiences_dir / f"exp_{_stamp()}_index.md",
                    old_idx.read_text(encoding="utf-8"),
                )
            except OSError:
                pass

    def list_paths(self) -> list[Path]:
        files = [p for p in self.experiences_dir.glob("exp_*.md") if p.is_file()]

        def sort_key(p: Path):
            m = _EXP_NAME_RE.match(p.name)
            stamp = m.group(1) if m else ""
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0.0
            return (stamp, mtime, p.name)

        return sorted(files, key=sort_key, reverse=True)

    def list_recent(self, limit: int = 20) -> list[Path]:
        return self.list_paths()[:limit]

    def latest_path(self) -> Path | None:
        paths = self.list_paths()
        return paths[0] if paths else None

    def is_empty(self) -> bool:
        latest = self.latest_path()
        if not latest:
            return True
        try:
            text = latest.read_text(encoding="utf-8").strip()
        except OSError:
            return True
        if not text:
            return True
        if "（暂无经验" in text and "## 实现步骤" not in text and "## 成功实现路径" not in text:
            return True
        # 至少要有 front matter 或某个核心章节
        if text.startswith("---") or "## 实现步骤" in text or "## 成功实现路径" in text or "## 执行顺序" in text:
            return False
        return len(text) < 80

    def read_latest_text(self, *, max_chars: int = 0) -> str:
        path = self.latest_path()
        if not path:
            return ""
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
        if max_chars > 0 and len(text) > max_chars:
            return text[:max_chars] + "\n…(截断)"
        return text

    def save(self, lesson: Lesson) -> Path:
        """始终新建带时间戳的经验文件（不覆盖旧文件）。"""
        lesson.created_at = lesson.created_at or _now()
        lesson.updated_at = _now()
        stamp = _stamp()
        fname = f"exp_{stamp}.md"
        path = self.experiences_dir / fname
        if path.exists():
            fname = f"exp_{stamp}_{lesson.id[-4:]}.md"
            path = self.experiences_dir / fname
        lesson.filename = f"{EXPERIENCES_SUBDIR}/{fname}"
        safe_write_text(path, render_lesson_md(lesson))
        return path

    def virtual_memory_paths(self, *, recent: int = 8) -> list[str]:
        paths = ["/memory/AGENTS.md"]
        latest = self.latest_path()
        if latest:
            rel = latest.relative_to(self.memory).as_posix()
            paths.append(f"/memory/{rel}")
        return paths

    def prompt_digest(self, *, limit: int = 5, max_chars: int = 3500) -> str:
        text = self.read_latest_text(max_chars=max_chars)
        if not text:
            return ""
        latest = self.latest_path()
        name = latest.name if latest else "latest"
        return (
            f"【项目经验记忆】以下来自最新经验 `{name}`（历史经验不自动注入；"
            "只关注摘要/成功路径/注意事项，忽略任何结果或产物描述）：\n\n"
            + text
            + "\n\n经验只含**成功路径**：按每步「操作+目的」理解，忽略任何失败/试错细节。\n"
        )

    def rebuild_index(self) -> None:
        self.experiences_dir.mkdir(parents=True, exist_ok=True)

    def open_in_browser(self) -> bool:
        path = self.latest_path()
        if not path:
            return False
        try:
            import webbrowser

            webbrowser.open(path.resolve().as_uri())
            return True
        except OSError:
            return False


def build_success_path_from_timeline_events(
    events: list,
    *,
    limit: int = 40,
) -> tuple[str, str, str]:
    """从时间线事件提炼 (success_path, summary, errors) — 作 AI 回退用。"""
    steps: list[str] = []
    agent_bits: list[str] = []
    errors: list[str] = []
    for ev in events or []:
        kind = getattr(ev, "kind", "") or ""
        content = (getattr(ev, "content", None) or "").strip()
        if not content:
            continue
        if kind == "tool":
            if len(steps) >= limit:
                continue
            line = content
            for prefix in (
                "call: ",
                "callback: ",
                "⟶ 调用工具：",
                "⟵ ",
            ):
                if line.startswith(prefix):
                    line = line[len(prefix) :].strip()
                    break
            if len(line) > 280:
                line = line[:280] + "…"
            if "本地脚本" in line and "成功" in line:
                line = re.split(r"成功[:：]", line, maxsplit=1)[0] + "成功"
            steps.append(f"{len(steps) + 1}. {line}")
        elif kind == "agent":
            agent_bits.append(content[:400])
        elif kind == "error":
            errors.append(content[:400])
        elif kind == "info" and ("失败" in content or "取消" in content):
            errors.append(content[:400])

    success_path = "\n".join(steps) if steps else ""
    summary = ""
    if steps:
        summary = f"本轮记录了 {len(steps)} 个工具/脚本相关流程步骤（不含结果正文）。"
    elif agent_bits:
        summary = "本轮以 Agent 过程说明为主，详见注意事项与实现步骤。"
    err_text = "\n".join(errors[-5:]) if errors else ""
    return success_path, summary, err_text


# --------------------------------------------------------------------------- #
# Agent 直接写入经验（工具路径）：Agent 用一手上下文直接落盘，不再跑独立总结大调用
# --------------------------------------------------------------------------- #

def merge_pipeline_steps(
    ai_steps: list[dict[str, Any]],
    solid_steps: list[Any],
    ai_written: list[Any],
) -> list[dict[str, Any]]:
    """合并「轨迹固化脚本」「AI 手写脚本」与 AI 给出的 pipeline_steps。

    AI 引用的 script 路径若在磁盘上不存在，尝试按文件名匹配轨迹固化或
    AI 手写产生的真实脚本；仍匹配不上时由 pipeline 固化层拒绝整条提案。
    AI 明确给出 pipeline_steps 时完全尊重其顺序，不再把未覆盖脚本前插，避免
    改变无 pipeline 探索出的真实脚本/AI 交错路径。AI 未给清单时才使用轨迹顺序。
    """
    merged: list[dict[str, Any]] = []
    for raw in ai_steps or []:
        if not isinstance(raw, dict):
            continue
        merged.append(raw)

    real_names: dict[str, str] = {}  # 小写文件名 → 规范化 scripts/ 路径
    for st in list(solid_steps or []) + list(ai_written or []):
        rel = str(getattr(st, "rel_path", "") or "")
        if rel:
            real_names[Path(rel).name.lower()] = rel

    covered: set[str] = set()
    for step in merged:
        if str(step.get("type") or "").lower() != "script":
            continue
        path = str(step.get("path") or "").replace("\\", "/").strip()
        base = Path(path).name.lower() if path else ""
        if base and base in real_names:
            step["path"] = real_names[base]  # AI 引用名对齐真实脚本
        covered.add(base)
        covered.add(str(step.get("path") or "").replace("\\", "/").strip().lower())

    if merged:
        return merged
    return [
        {
            "type": "script",
            "path": getattr(st, "rel_path", "") or "",
            "tool": getattr(st, "tool", "") or "script",
            "description": (getattr(st, "description", "") or "轨迹固化脚本")[:200],
            "args": getattr(st, "args", {}) or {},
        }
        for st in list(solid_steps or [])
        if getattr(st, "rel_path", "")
    ]


def write_ai_lesson(
    project_root: Path,
    *,
    project_id: str,
    goal: str,
    outcome: str = "success",
    summary: str,
    success_path: str = "",
    notes: str = "",
    errors: str = "",
    script_files: list[dict[str, Any]] | None = None,
    pipeline_steps: list[dict[str, Any]] | None = None,
    used_skills: list[str] | None = None,
    reference_materials: list[dict[str, Any]] | None = None,
    model_label: str = "",
    policy: str = "",
    events: list[Any] | None = None,
) -> Lesson:
    """把 Agent 给出的经验内容直接固化为经验文档 + pipeline + 脚本（不调 LLM）。

    复用与结束总结完全相同的固化管线（solidify/AI 手写脚本/pipeline/Skills 快照/
    参考材料登记），保证后续运行可严格照章执行。任何一步固化失败不影响经验落盘。
    """
    from wokbee.core.references import snapshot_used_skills as _snap_skills
    from wokbee.core.references import write_reference_manifest as _write_manifest
    from wokbee.engine.script_factory import (
        apply_ai_authored_scripts,
        apply_ai_pipeline_steps,
        solidify_scripts,
        drop_missing_pipeline_scripts,
    )

    root = Path(project_root)
    store = LessonStore(root)
    lesson = Lesson(
        project_id=project_id,
        goal=goal or "",
        outcome=outcome or "success",
        summary=(summary or "").strip() or "本轮流程经验（方法向，不含结果/产物）。",
        success_path=(success_path or "").strip(),
        notes=(notes or "").strip(),
        errors=(errors or "").strip(),
        model=model_label,
        policy=policy,
    )

    trace = lesson.success_path
    try:
        solid = solidify_scripts(
            root,
            lesson_id=lesson.id,
            goal=lesson.goal,
            summary=lesson.summary,
            success_path=trace,
            events=list(events or []),
        )
        ai_written = apply_ai_authored_scripts(
            root,
            lesson_id=lesson.id,
            project_id=project_id,
            script_files=script_files or [],
        )
        rename_map: dict[str, str] = {}
        for st in ai_written:
            src = str((st.args or {}).get("_src") or "").strip()
            if src:
                rename_map[src] = Path(st.rel_path).name
        applied_order = apply_ai_pipeline_steps(
            root,
            lesson_id=lesson.id,
            goal=lesson.goal,
            pipeline_steps=merge_pipeline_steps(
                pipeline_steps or [],
                solid.script_steps,
                ai_written,
            ),
            rename_map=rename_map or None,
        )
        lesson.scripts = [s.rel_path for s in solid.script_steps] + [
            s.rel_path for s in ai_written
        ]
        seen: set[str] = set()
        uniq: list[str] = []
        for p in lesson.scripts:
            if p and p not in seen:
                seen.add(p)
                uniq.append(p)
        lesson.scripts = uniq
        lesson.pipeline = solid.pipeline_rel
        drop_missing_pipeline_scripts(root)
        sync_lesson_from_pipeline(lesson, root)
        _ = applied_order
    except Exception:
        import logging

        logging.getLogger("wokbee").exception(
            "update_project_experience：固化脚本/管线失败（经验仍会写入）"
        )

    try:
        written = _snap_skills(root, [s for s in (used_skills or []) if str(s).strip()])
        mats = [
            m
            for m in (reference_materials or [])
            if isinstance(m, dict)
            and (str(m.get("path") or "").strip() or str(m.get("note") or "").strip())
        ]
        manifest_path = _write_manifest(
            root,
            used_skills=[str(s) for s in (used_skills or []) if str(s).strip()],
            materials=mats,
            goal=lesson.goal,
        )
        _ = written, manifest_path
    except Exception:
        import logging

        logging.getLogger("wokbee").exception("update_project_experience：保存参考材料失败")

    store.save(lesson)
    return lesson


def build_experience_tools(
    *,
    project_id: str,
    project_root: Path,
    goal: str = "",
    model_label: str = "",
    policy: str = "",
    emit=None,
    on_written=None,
    events_provider=None,
):
    """构造经验写入工具：update_project_experience（Agent 直接固化经验/管线/脚本）。

    on_written: 工具成功写入后回调（无参），runner 用它标记「本轮经验已由 Agent 更新」，
    结束兜底据此跳过自动总结。
    events_provider: 返回本轮运行事件快照的可调用对象；solidify 需要真实执行轨迹
    才能识别已跑过的命令；用户上传脚本优先直接引用 uploads/，不复制到项目根目录，
    其他可复用命令才固化成 scripts/ 下的脚本文件——缺失时 AI 又未给 script_files，
    pipeline 就会引用幽灵脚本，二次运行报「文件不存在」。
    """

    def _notify(kind: str, content: str, meta: dict | None = None) -> None:
        if emit:
            try:
                emit(kind, content, meta or {})
            except Exception:
                pass

    from langchain_core.tools import tool
    from wokbee.engine.script_factory import drop_missing_pipeline_scripts

    @tool
    def update_project_experience(
        summary: str,
        success_path: str = "",
        notes: str = "",
        outcome: str = "success",
        errors: str = "",
        script_files: list[dict] | None = None,
        pipeline_steps: list[dict] | None = None,
        used_skills: list[str] | None = None,
        reference_materials: list[dict] | None = None,
    ) -> str:
        """把本轮可复用的方法经验固化写入项目经验（memory/experiences/），并更新 pipeline.json 与 scripts/。

        必须调用的场景：
        - 首次运行（尚无 pipeline.json/经验）收尾时：把本次验证过的流程固化为经验与管线；
        - 运行中脚本报错/数据异常被你修复后：把修正方法写入经验，并修正 pipeline/脚本，供下次稳定复跑；
        - 你发现已有经验/管线明显过时或有更优方法时。

        字段要求（后续运行会**严格照章执行**，务必只写真实验证过、可复用、尽量精简的内容）：
        - summary: 经验摘要（概要介绍经验作用，非结果正文）
        - success_path: 有序成功步骤，每步格式 `序号. 执行角色: "{执行内容}"; 【步骤说明】`
          （执行角色=AI/工具调用/脚本执行/系统执行；剔除失败/试错步骤）
        - notes: 注意事项，无序列表，每条 `- **问题**：解决办法`
        - outcome: success | failed
        - script_files: 需固化的脚本 [{"filename","content","description","in_pipeline"}]——
          **每个可复用命令/脚本都要在这里给出完整源码**（.py/.bat/.ps1 等），没有则省略
        - pipeline_steps: 有序步骤 [{"type":"script","path":"uploads/..." 或 "scripts/...","description"} 或
          {"type":"ai","description":"明确业务任务","prompt_hint":"..."}]，无则省略。
          **每个 script 步骤的 path 必须对应真实脚本文件**（来自 uploads/、script_files 或 scripts/ 已有文件），
          且必须是 `scripts/...` 或 `uploads/...` 相对虚拟路径；系统写入后会重新读取并验证，失败则拒绝保存。
        - used_skills: 用到的全局 Skill 目录名；reference_materials: 需存 uploads/references/ 的材料
        """
        if not (summary or "").strip():
            return "错误：summary 不能为空"
        tool_events = None
        if callable(events_provider):
            try:
                tool_events = events_provider() or None
            except Exception:
                tool_events = None
        try:
            lesson = write_ai_lesson(
                project_root,
                project_id=project_id,
                goal=goal,
                outcome=outcome if outcome in ("success", "failed") else "success",
                summary=summary,
                success_path=success_path,
                notes=notes,
                errors=errors,
                script_files=list(script_files or []),
                pipeline_steps=list(pipeline_steps or []),
                used_skills=list(used_skills or []),
                reference_materials=list(reference_materials or []),
                model_label=model_label,
                policy=policy,
                events=tool_events,
            )
        except Exception as e:
            _notify("error", f"写入项目经验失败：{e}")
            return f"错误：写入项目经验失败：{e}"
        if on_written:
            try:
                on_written()
            except Exception:
                pass
        rel = lesson.filename or "memory/experiences/"
        result = (
            f"项目经验已写入 `{rel}`"
            + (f"，固化脚本 {len(lesson.scripts)} 个" if lesson.scripts else "")
            + "；pipeline.json 已更新，下次运行按 steps 执行。"
        )
        try:
            dropped = drop_missing_pipeline_scripts(project_root)
        except Exception:
            import logging

            logging.getLogger("wokbee").exception("清理幽灵管线脚本失败")
            dropped = []
        if dropped:
            warn = (
                "警告：以下管线步骤引用的脚本文件不存在，已从 pipeline.json 移除："
                + "、".join(dropped)
                + "。若这些步骤必要，请重新调用本工具：在 script_files 中给出每个脚本的"
                "完整源码（filename+content），并在 pipeline_steps 里引用对应文件名。"
            )
            _notify("info", warn)
            result += "\n" + warn
        _notify(
            "lesson",
            f"Agent 已固化项目经验：`{rel}`"
            + (f"（脚本 {len(lesson.scripts)} 个已入管线）" if lesson.scripts else ""),
            {"lesson_id": lesson.id, "path": str(Path(project_root) / rel)},
        )
        return result

    return [update_project_experience]
