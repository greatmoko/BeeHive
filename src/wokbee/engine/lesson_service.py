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
from wokbee.engine.lesson_events import (
    build_lesson_digest,
    collect_events_log,
    slice_latest_round,
)
from wokbee.engine.lesson_storage import LessonStore
from wokbee.engine.lesson_ai import (
    _AI_SUMMARY_JUDGE_SYSTEM,
    _AI_SUMMARY_SYSTEM,
    _dedupe_identical_pipeline_steps,
    _extract_json_object,
    _normalize_ai_pipeline_steps,
    _normalize_ai_reference_materials,
    _normalize_ai_script_files,
    _normalize_ai_used_skills,
    _parse_ai_summary_json,
    judge_should_update_experience,
    summarize_lesson_with_ai,
)
from wokbee.engine.lesson_pipeline import (
    build_success_path_from_pipeline,
    sync_lesson_from_pipeline,
)
from wokbee.engine.lesson_summary import (
    build_success_path_from_timeline_events,
    merge_pipeline_steps,
    write_ai_lesson,
)
from wokbee.engine.lesson_tools import build_experience_tools

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
