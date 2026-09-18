"""AutoBee UI shared status labels and time formatting."""

from __future__ import annotations

from autobee.core.models import TaskRunStatus

_STATUS_COLOR = {
    TaskRunStatus.SUCCESS: "success",
    TaskRunStatus.FAILED: "danger",
    TaskRunStatus.MISSED: "warning",
    TaskRunStatus.RUNNING: "accent",
}


def _time_part(ts: str) -> str:
    s = (ts or "").strip()
    if not s:
        return "—"
    parts = s.split()
    return parts[1] if len(parts) > 1 else s


def _date_time(ts: str) -> str:
    return (ts or "").strip() or "—"


def _status_label(raw: str) -> str:
    if not raw:
        return "未运行"
    try:
        return TaskRunStatus(raw).label
    except ValueError:
        return raw


__all__ = ["_STATUS_COLOR", "_time_part", "_date_time", "_status_label"]
