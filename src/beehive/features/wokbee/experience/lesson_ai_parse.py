"""经验模型输出 JSON 解析与脚本、步骤、引用校验。"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any

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
            if path and not path.startswith("memory/"):
                path = f"memory/{Path(path).name}"
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
