"""AutoBee 运行日志列表与详情卡片."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout

from tokbee.ui.combo_style import secondary_btn_qss
from tokbee.ui.styles.theme import Theme

from autobee.core.models import JobLog
from autobee.ui.autobee_ui_common import _STATUS_COLOR, _date_time, _time_part

class _LogDetailDialog(QDialog):
    """运行详情弹窗。"""

    def __init__(self, theme: Theme, log: JobLog, task_name: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("运行详情")
        self.setMinimumSize(520, 420)
        self.resize(560, 480)
        c = theme.colors
        self.setStyleSheet(f"background: {c['content_bg']};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(10)

        title = QLabel(task_name or "定时任务")
        title.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {c['text']};"
            "background: transparent; border: none;"
        )
        lay.addWidget(title)

        color_key = _STATUS_COLOR.get(log.status, "text_secondary")
        status_color = c.get(color_key, c["text_secondary"])
        lines = [
            f"状态：{log.status.label}",
            f"开始：{_date_time(log.started_at)}",
            f"结束：{_date_time(log.finished_at)}",
        ]
        if log.duration_s:
            lines.append(f"耗时：{log.duration_s:.1f}s")
        meta = QLabel("    ".join(lines))
        meta.setWordWrap(True)
        meta.setStyleSheet(
            f"font-size: 12px; color: {status_color};"
            "background: transparent; border: none;"
        )
        lay.addWidget(meta)

        body = QTextEdit()
        body.setReadOnly(True)
        parts = []
        if log.summary:
            parts.append(log.summary)
        if log.error:
            parts.append(f"错误：\n{log.error}")
        if log.meta:
            try:
                import json
                parts.append("元数据：\n" + json.dumps(log.meta, ensure_ascii=False, indent=2))
            except Exception:
                parts.append(f"元数据：{log.meta}")
        body.setPlainText("\n\n".join(parts) if parts else "（无详细内容）")
        body.setStyleSheet(f"""
            QTextEdit {{
                background: {c["input_bg"]}; color: {c["text"]};
                border: 1px solid {c["input_border"]}; border-radius: 8px;
                padding: 10px; font-size: 13px;
            }}
        """)
        lay.addWidget(body, stretch=1)

        row = QHBoxLayout()
        row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setFixedSize(80, 34)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(secondary_btn_qss(c))
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        lay.addLayout(row)


def _status_label(raw: str) -> str:
    if not raw:
        return "未运行"
    try:
        return TaskRunStatus(raw).label
    except ValueError:
        return raw


class _LogRow(QFrame):
    """列表行：运行时间 / 任务名 / 完成情况。"""

    def __init__(self, theme: Theme, log: JobLog, task_name: str, parent=None):
        super().__init__(parent)
        c = theme.colors
        self.setObjectName("logRow")
        self.setStyleSheet(f"""
            QFrame#logRow {{
                background: transparent; border: none;
            }}
            QFrame#logRow QLabel {{
                background: transparent; border: none;
            }}
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(10)

        time_lbl = QLabel(_time_part(log.finished_at or log.started_at))
        time_lbl.setFixedWidth(64)
        time_lbl.setStyleSheet(f"font-size: 12px; color: {c['text_secondary']};")
        lay.addWidget(time_lbl)

        name_lbl = QLabel(task_name or "—")
        name_lbl.setStyleSheet(f"font-size: 13px; color: {c['text']}; font-weight: 600;")
        name_lbl.setMinimumWidth(80)
        lay.addWidget(name_lbl, stretch=2)

        color_key = _STATUS_COLOR.get(log.status, "text_secondary")
        status_color = c.get(color_key, c["text_secondary"])
        status_lbl = QLabel(log.status.label)
        status_lbl.setFixedWidth(48)
        status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_lbl.setStyleSheet(
            f"font-size: 12px; color: {status_color}; font-weight: bold;"
        )
        lay.addWidget(status_lbl)

        dur = f"{log.duration_s:.1f}s" if log.duration_s else "—"
        dur_lbl = QLabel(dur)
        dur_lbl.setFixedWidth(52)
        dur_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        dur_lbl.setStyleSheet(f"font-size: 12px; color: {c['text_hint']};")
        lay.addWidget(dur_lbl)

        brief = (log.error or log.summary or "").replace("\n", " ").strip()
        if len(brief) > 36:
            brief = brief[:36] + "…"
        brief_lbl = QLabel(brief or "—")
        brief_lbl.setStyleSheet(f"font-size: 12px; color: {c['text_hint']};")
        lay.addWidget(brief_lbl, stretch=3)

    def sizeHint(self) -> QSize:
        return QSize(200, 44)



__all__ = ["_LogDetailDialog", "_LogRow"]

