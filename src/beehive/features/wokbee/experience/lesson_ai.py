"""经验模型输入、流式收集与失败回退。"""
from __future__ import annotations
from beehive.system.prompts import (
    EXPERIENCE_SUMMARY_SYSTEM_PROMPT as _AI_SUMMARY_SYSTEM,
    EXPERIENCE_UPDATE_SYSTEM_PROMPT as _AI_SUMMARY_JUDGE_SYSTEM,
)
from typing import Any
from beehive.features.wokbee.experience.lesson_ai_parse import _parse_ai_summary_json, _normalize_ai_script_files, _normalize_ai_used_skills, _normalize_ai_reference_materials

def summarize_lesson_with_ai(
    *,
    model: Any,
    goal: str,
    outcome: str,
    previous_experience: str,
    run_log: str,
    scripts_context: str,
    environment_hint: str = "",
) -> dict[str, Any]:
    """调用模型总结经验；失败时抛出异常由调用方回退。

    返回字段均为 str，另含：
    - script_files: list[dict]（AI 手写脚本，可为空）
    - used_skills: list[str]（本次用到的 Skill 目录名）
    - reference_materials: list[dict]（需保存进 memory/ 的参考材料）
    生成过程在内部收齐，结束后一次性返回，避免向时间线刷进度气泡。
    """
    user = (
        f"项目目标：{goal or '（未设置）'}\n"
        f"本轮 outcome：{outcome}\n\n"
        f"## 上一份经验（可能为空）\n{previous_experience or '（无）'}\n\n"
        f"## 本次运行日志\n{run_log}\n\n"
        f"## 现有脚本\n{scripts_context}\n\n"
        f"## 环境提示\n{environment_hint or '（无）'}\n\n"

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
    out["used_skills"] = _normalize_ai_used_skills(data.get("used_skills"))
    out["reference_materials"] = _normalize_ai_reference_materials(
        data.get("reference_materials")
    )
    return out

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
