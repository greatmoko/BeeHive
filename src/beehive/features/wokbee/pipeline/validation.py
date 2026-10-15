"""按职责组织的 validation。"""
from __future__ import annotations
import logging
import json
import re
from pathlib import Path
from beehive.features.wokbee.projects.paths import memory_dir
logger = logging.getLogger("wokbee")


def load_pipeline(project_root: Path) -> dict | None:
    path = memory_dir(project_root) / "pipeline.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("读取 pipeline.json 失败: %s", e)
        return None


def resolve_pipeline_script_path(project_root: Path, rel_path: str) -> Path | None:
    """按 Pipeline 合同解析脚本：仅接受项目内 memory/ 或 uploads/ 相对路径。"""
    root = Path(project_root).resolve()
    raw = str(rel_path or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:/", raw):
        return None
    parts = raw.split("/")
    if ".." in parts or parts[0].lower() not in {"memory", "uploads"}:
        return None
    candidate = (root / "/".join(parts)).resolve()
    allowed = (root / parts[0]).resolve()
    try:
        candidate.relative_to(allowed)
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def validate_pipeline_script_paths(
    project_root: Path,
    steps: list[dict] | None = None,
) -> list[str]:
    """返回 pipeline 中缺失或越界的脚本路径，不执行任何脚本。"""
    root = Path(project_root).resolve()
    if steps is None:
        data = load_pipeline(root)
        steps = data.get("steps") if isinstance(data, dict) else []
    missing: list[str] = []
    for step in steps or []:
        if not isinstance(step, dict) or str(step.get("type") or "").lower() != "script":
            continue
        rel = str(step.get("path") or "").replace("\\", "/").strip()
        valid = resolve_pipeline_script_path(root, rel) is not None
        if not valid:
            missing.append(rel or "（空路径步骤）")
    return missing


def normalize_steps(data: dict, *, keep_ai: bool = True) -> list[dict]:
    """统一为可运行的有序 steps（script 与 ai 均可）。

    兼容旧版 scripts + ai_steps。新策略：pipeline 固化首次成功后的完整执行路径——
    `type:script` 确定性机械步骤直接执行（不耗 Token）；`type:ai` 是**已经确定的业务任务**
    （理解/分析/整理/创意/写作等），后续运行照样调用 LLM 执行该固定任务，但禁止重新规划。
    keep_ai=False 时过滤 ai 步骤（仅用于展示/统计）。
    """
    raw = data.get("steps")
    if isinstance(raw, list) and raw:
        out: list[dict] = []
        for i, s in enumerate(raw):
            if not isinstance(s, dict):
                continue
            t = str(s.get("type") or "").lower().strip()
            if t not in ("script", "ai"):
                continue
            if t == "ai" and not keep_ai:
                continue
            step = dict(s)
            step["type"] = t
            step.setdefault("id", f"{t}_{i+1}")
            out.append(step)
        if out:
            return out

    # 兼容：先全部脚本，再全部 AI（同样过滤 ai）
    steps: list[dict] = []
    for i, entry in enumerate(data.get("scripts") or []):
        if not isinstance(entry, dict):
            continue
        steps.append(
            {
                "id": f"script_{i+1}",
                "type": "script",
                "path": entry.get("path") or "",
                "tool": entry.get("tool") or "",
                "description": entry.get("description") or entry.get("path") or "",
                "args": entry.get("args") or {},
            }
        )
    if not keep_ai:
        return steps
    for i, entry in enumerate(data.get("ai_steps") or []):
        if isinstance(entry, dict):
            steps.append(
                {
                    "id": f"ai_{i+1}",
                    "type": "ai",
                    "description": entry.get("description") or "AI 步骤",
                    "prompt_hint": entry.get("prompt_hint") or "",
                }
            )
        else:
            steps.append(
                {
                    "id": f"ai_{i+1}",
                    "type": "ai",
                    "description": str(entry),
                    "prompt_hint": "",
                }
            )
    return steps


def group_phases(steps: list[dict]) -> list[dict]:
    """按 pipeline 数组保留逐步边界，不把连续 script/ai 合并。"""
    return [
        {"type": step.get("type"), "steps": [step]}
        for step in steps
        if isinstance(step, dict)
    ]


def format_order_markdown(steps: list[dict]) -> str:
    """生成 pipeline.json 的 order_markdown 字段（执行顺序说明）。"""
    if not steps:
        return "（暂无有序步骤；下次运行将走完整 AI。）"
    lines = [
        "下次运行将**严格按下列顺序一路执行**：",
        "（script 步骤自动执行、不耗 Token；ai 步骤执行已确定的 AI 业务任务）",
        "",
    ]
    for i, s in enumerate(steps, 1):
        t = s.get("type")
        if t == "script":
            lines.append(
                f"{i}. **[脚本]** `{s.get('path')}` — {s.get('description') or ''}"
            )
        else:
            lines.append(f"{i}. **[AI]** {s.get('description') or 'AI 步骤'}")
            if s.get("prompt_hint"):
                lines.append(f"   - 提示：{s.get('prompt_hint')}")
    lines.append("")
    lines.append("`memory/` **不参与归档**；本顺序保存在 `memory/pipeline.json` 的 `steps` 中。")
    return "\n".join(lines)
