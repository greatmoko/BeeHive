"""AI 管线提案的脚本路径及步骤校验。"""
from __future__ import annotations
from pathlib import Path
from typing import Any
from beehive.features.wokbee.pipeline.validation import resolve_pipeline_script_path

def _resolve_pipeline_script_path(
    root: Path,
    sdir: Path,
    raw_path: str,
    *,
    rename_map: dict[str, str] | None = None,
) -> str | None:
    """解析 AI 脚本引用，只接受项目 memory/ 或 uploads/ 相对路径。"""

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
    if len(parts) < 2 or parts[0].lower() not in {"memory", "uploads"}:
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


def normalize_pipeline_steps(root: Path, sdir: Path, steps_in: list, rename_map: dict[str, str] | None):
    """校验提案并归一化步骤；非法空路径拒绝整条提案，缺失引用由提交层统一拒绝。"""
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
                return None
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
    return normalized, missing_script_paths
