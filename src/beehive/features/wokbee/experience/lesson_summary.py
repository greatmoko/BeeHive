"""经验摘要：从运行时间线提炼可复用的成功路径。"""

from __future__ import annotations

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



from pathlib import Path
from typing import Any
import re

from beehive.features.wokbee.experience.lesson_models import Lesson
from beehive.features.wokbee.experience.lesson_storage import LessonStore

_SCRIPT_EXT = r"(?:py|bat|cmd|ps1|sh|js|json)"
# 注：扩展名后必须跟非单词字符（\W 或行尾），避免把 memory/events.jsonl 误匹配成 memory/events.js
_MEMORY_SCRIPT_REF = re.compile(rf"memory/[\w\-.\u4e00-\u9fff]+\.{_SCRIPT_EXT}(?![\w.])", re.IGNORECASE)
_SCRIPT_PATH_LINE = re.compile(r"^\s*(\d+)[.、．]\s*(.*)$")
_SCRIPT_NOTE = re.compile(r"【([^】]*)】")


def _step_match_tokens(step) -> list[str]:
    """从固化步骤的参数中提取可与成功路径文本匹配的 token（小写）。"""
    args = getattr(step, "args", None) or {}
    tokens: set[str] = set()
    url = str(args.get("url") or "")
    if url:
        m = re.match(r"https?://([^/?#]+)", url)
        if m:
            for part in m.group(1).lower().split("."):
                if len(part) >= 2 and part not in {"com", "cn", "net", "org", "gov", "edu", "www"}:
                    tokens.add(part)
        tail = url.split("?", 1)
        if len(tail) == 2:
            for kv in tail[1].split("&"):
                key = kv.split("=", 1)[0]
                if len(key) >= 4:
                    tokens.add(key.lower())
    for field in ("query", "keyword", "q"):
        val = str(args.get(field) or "")
        for w in re.findall(r"[\w\u4e00-\u9fff]{2,}", val):
            tokens.add(w.lower())
    for w in re.findall(r"[\w.\-/\\]{3,}", str(args.get("command") or "")):
        tokens.add(w.lower())
    src = str(args.get("_src") or "")
    if src:
        tokens.add(src.lower())
    return sorted(t for t in tokens if len(t) >= 2)


def _map_scripts_into_success_path(lesson: Lesson, steps: list) -> None:
    """把成功路径中对应工具调用的步骤改写为“脚本执行: {固化脚本地址}”。

    匹配：用步骤参数中的 URL 域名/query/命令 token 找最佳命中行（每行只占用一次）；
    改写保留原序号与【备注】，仅替换执行角色与执行内容，保证下次运行可直接调取脚本。
    """
    lines = (lesson.success_path or "").splitlines()
    used: set[int] = set()
    for st in steps:
        rel = str(getattr(st, "rel_path", "") or "")
        if not rel:
            continue
        tokens = _step_match_tokens(st)
        if not tokens:
            continue
        best_i, best_score = -1, 0
        for i, line in enumerate(lines):
            if i in used or rel in line or "脚本执行" in line:
                continue
            low = line.lower()
            score = sum(1 for t in tokens if t in low)
            if score > best_score:
                best_i, best_score = i, score
        if best_i < 0:
            continue
        m = _SCRIPT_PATH_LINE.match(lines[best_i])
        num = m.group(1) if m else str(best_i + 1)
        note_m = _SCRIPT_NOTE.search(lines[best_i])
        note = note_m.group(1) if note_m else ""
        lines[best_i] = f'{num}. 脚本执行: "{rel}"' + (f"【{note}】" if note else "")
        used.add(best_i)
    lesson.success_path = "\n".join(lines)

def solidify_lesson_scripts(
    project_root: Path,
    lesson: Lesson,
    *,
    success_path: str,
    events: list | None = None,
    script_files: list[dict] | None = None,
) -> int:
    """两条经验写入路径共用：固化脚本到 memory/ 并校验脚本真实存在。

    - 轨迹固化：从执行事件与成功路径提取真实跑过的命令，渲染成脚本写入 memory/。
    - AI 手写脚本：把 script_files 提供的完整源码写入 memory/。
    - 校验：lesson.scripts 中每个脚本必须真实存在于磁盘，否则抛错、经验不落盘。

    返回 AI 手写脚本数量；固化结果同步回填 lesson.scripts（相对虚拟路径）。
    """
    from beehive.features.wokbee.pipeline.script_ai import apply_ai_authored_scripts
    from beehive.features.wokbee.pipeline.script_solidification import solidify_scripts

    root = Path(project_root)

    # 先拒绝幽灵引用：success_path 引用的 memory/ 脚本必须真实存在，
    # 或由本轮 script_files 提供（随后会重写为固化后的真实路径）。
    ai_names = {
        str(item.get("filename") or "").strip().lower()
        for item in (script_files or [])
        if isinstance(item, dict)
    }
    ghosts = [
        ref for ref in _MEMORY_SCRIPT_REF.findall(success_path or "")
        if not (root / ref).is_file()
        and Path(ref).name.lower() not in ai_names
    ]
    if ghosts:
        raise ValueError(
            "脚本固化校验失败，以下脚本未成功写入磁盘，经验未保存："
            + "、".join(sorted(set(ghosts)))
            + "。请修正 script_files 的源码后重试。"
        )

    solid = solidify_scripts(
        root,
        lesson_id=lesson.id,
        goal=lesson.goal,
        summary=lesson.summary,
        success_path=success_path,
        events=events,
    )
    ai_written = apply_ai_authored_scripts(
        root,
        lesson_id=lesson.id,
        project_id=lesson.project_id,
        script_files=script_files or [],
    )

    scripts: list[str] = []
    for st in list(solid.script_steps) + list(ai_written):
        rel = str(getattr(st, "rel_path", "") or "")
        if rel and rel not in scripts:
            scripts.append(rel)

    # 系统校验：脚本必须真实存在于 memory/ 或 uploads/，拒绝幽灵脚本
    missing = [
        rel for rel in scripts
        if not (root / rel).is_file()
    ]
    if missing:
        lesson.scripts = []
        raise ValueError(
            "脚本固化校验失败，以下脚本未成功写入磁盘，经验未保存："
            + "、".join(missing)
            + "。请修正 script_files 的源码后重试。"
        )
    lesson.scripts = scripts

    # 正文“可复用脚本”章节：列出固化脚本与用途，供下次运行直接调取。
    entries = [
        f"- `{st.rel_path}` — {getattr(st, 'description', '') or ''}".rstrip(" —")
        for st in list(solid.script_steps) + list(ai_written)
        if getattr(st, "rel_path", "")
    ]
    if entries:
        lesson.script_section = (
            "\n".join(entries)
            + "\n\n约定：每个脚本执行后把 callback 写入 `script_callback_<脚本名>.md`（项目根目录）；"
            "需要交付的文件复制到 output/（保留原始文件，不合并成 final.md）。"
        )
    else:
        lesson.script_section = "（本轮无可固化脚本。）"

    # 把 success_path 中对应工具调用的步骤改写为固化脚本地址（AI 手写脚本用 _src 精确替换）。
    for st in list(solid.script_steps) + list(ai_written):
        rel = str(getattr(st, "rel_path", "") or "")
        src = str((getattr(st, "args", None) or {}).get("_src") or "")
        if rel and src:
            lesson.success_path = lesson.success_path.replace(src, rel)
    _map_scripts_into_success_path(lesson, list(solid.script_steps) + list(ai_written))
    return len(ai_written)


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
    used_skills: list[str] | None = None,
    reference_materials: list[dict[str, Any]] | None = None,
    model_label: str = "",
    policy: str = "",
    events: list[Any] | None = None,
) -> Lesson:
    """把 Agent 给出的经验内容直接固化为经验文档 + memory/ 脚本（不调 LLM）。

    脚本固化失败（如源码为空、写盘失败、脚本不存在）时不写入经验。
    """
    from beehive.features.wokbee.projects.references import snapshot_used_skills as _snap_skills
    from beehive.features.wokbee.projects.references import write_reference_manifest as _write_manifest

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

    solidify_lesson_scripts(
        root,
        lesson,
        success_path=lesson.success_path,
        events=list(events or []),
        script_files=script_files,
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
