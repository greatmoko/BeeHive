"""按职责组织的 cron_description。"""
from __future__ import annotations
from beehive.features.autobee.core.store import AutoBeeStore


def describe_cron(expr: str) -> str:
    """把常见 5 段 cron 转成人类可读说明；无法识别时返回空串。"""
    fields = (expr or "").split()
    if len(fields) != 5:
        return ""
    minute, hour, day, month, dow = fields
    try:
        minute_i, hour_i = int(minute), int(hour)
    except ValueError:
        minute_i = hour_i = None
    # 分钟精度 / 固定时刻
    if minute == "*" and hour == "*" and day == "*" and month == "*" and dow == "*":
        return "每分钟"
    if minute.startswith("*/") and hour == "*" and _all_star(day, month, dow):
        try:
            return f"每 {int(minute[2:])} 分钟"
        except ValueError:
            pass
    if _all_star(minute, day, month, dow) and hour == "*":
        return "每小时"
    if _all_star(minute, hour, day, month, dow):
        return "每天任意时刻"
    # 每天固定时刻
    if _all_star(day, month, dow) and minute_i is not None and hour_i is not None:
        hm = f"{hour_i:02d}:{minute_i:02d}"
        return f"每天 {hm}"
    # 工作日固定时刻
    if day == "*" and month == "*" and dow == "1-5" and minute_i is not None and hour_i is not None:
        return f"每个工作日 {hour_i:02d}:{minute_i:02d}"
    if month == "*" and dow == "*" and _is_int(day) and _is_int(hour) and _is_int(minute):
        return f"每月 {int(day)} 号 {int(hour):02d}:{int(minute):02d}"
    return ""


def _all_star(*fields: str) -> bool:
    return all(f == "*" for f in fields)


def _is_int(s: str) -> bool:
    try:
        int(s)
        return True
    except (TypeError, ValueError):
        return False


def task_summary(store: AutoBeeStore, task_id: str) -> str:
    task = store.get(task_id)
    return f"{task.name} 执行出错" if task else "执行出错"
