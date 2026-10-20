"""经验模型输出 JSON 解析与脚本、步骤、引用校验。"""
from __future__ import annotations
import json
import re
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
        out.append(
            {
                "filename": filename,
                "content": content,
                "description": desc,
            }
        )
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
