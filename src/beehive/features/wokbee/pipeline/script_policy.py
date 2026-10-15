"""目标校验和交付物发布策略。"""
from __future__ import annotations
_DELIVERABLE_HINTS = ("output", "deliverables", "交付")

_NO_CHECK_HINTS = (
    "不校验", "不要校验", "无需校验", "不检查", "不要检查", "无需检查",
    "不验证", "不要验证", "无需验证", "不审查", "不要审查", "不审阅", "不要审阅",
    "不需要检查", "不需要校验", "不需要验证",
)

def goal_wants_deliverables(goal: str) -> bool:
    """目标是否要求把产物放进 output/（决定是否自动补一个确定性发布步骤）。

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
    """本次执行中 AI/工具是否真的把文件写到了 output/。"""
    for ev in events or []:
        meta = getattr(ev, "meta", None) or {}
        if not isinstance(meta, dict):
            meta = {}
        args = meta.get("args") if isinstance(meta.get("args"), dict) else {}
        blob = " ".join(str(v) for v in args.values()) if args else ""
        content = (getattr(ev, "content", None) or "") if not blob else ""
        low = f"{blob} {content}".lower()
        if "output" in low or "deliverables" in low:
            return True
    return False
