"""项目名称、目标和审核策略的展示控件。"""
from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from beehive.common.ui.theme import Theme
from beehive.features.wokbee.projects.models import Project
from beehive.features.wokbee.ui.project_status import STATUS_LABEL, STATUS_COLOR_KEY
class _ProjectEssentials(QFrame):
    goal_edit_requested = Signal()
    approval_edit_requested = Signal()
    ai_refine_requested = Signal()

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._build()

    def _build(self):
        c = self.theme.colors
        self.setStyleSheet(f"""
            _ProjectEssentials {{
                background: {c["card_bg"]};
                border-bottom: 1px solid {c["border"]};
            }}
        """)
        self.setMinimumHeight(88)
        self.setMaximumHeight(120)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(6)

        row1 = QHBoxLayout()
        self._status = QLabel("")
        self._status.setStyleSheet(f"font-size: 12px; color: {c['text_secondary']};")
        row1.addWidget(self._status)
        self._title = QLabel("未选择项目")
        self._title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {c['text']};")
        row1.addWidget(self._title)

        self._ai_refine_btn = QPushButton("✨")
        self._ai_refine_btn.setToolTip("AI 更新项目名与目标")
        self._ai_refine_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ai_refine_btn.setFixedSize(32, 34)
        self._ai_refine_btn.setEnabled(False)
        self._ai_refine_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_bg"]}; color: {c["text"]};
                border: none; border-radius: 6px; font-size: 14px;
            }}
            QPushButton:hover {{ background: {c["btn_hover"]}; }}
            QPushButton:disabled {{ color: {c["text_hint"]}; }}
        """)
        self._ai_refine_btn.clicked.connect(self.ai_refine_requested.emit)
        row1.addWidget(self._ai_refine_btn)
        row1.addStretch(1)

        self._policy_btn = QPushButton("审核策略")
        self._policy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._policy_btn.setFixedHeight(34)
        self._policy_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_bg"]}; color: {c["text"]};
                border: none; border-radius: 6px; padding: 0 10px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {c["btn_hover"]}; }}
        """)
        self._policy_btn.clicked.connect(self.approval_edit_requested.emit)
        row1.addWidget(self._policy_btn)
        layout.addLayout(row1)

        self._goal = QLabel("")
        self._goal.setWordWrap(True)
        self._goal.setMaximumHeight(self._goal.fontMetrics().lineSpacing() * 2 + 2)
        self._goal.setStyleSheet(f"font-size: 12px; color: {c['text_secondary']};")
        self._goal.setCursor(Qt.CursorShape.PointingHandCursor)
        self._goal.mousePressEvent = lambda e: self.goal_edit_requested.emit()  # type: ignore
        layout.addWidget(self._goal)
        self._goal_raw = ""

    def _elide_goal(self, text: str) -> str:
        """把目标文本裁剪到最多两行（超出末尾加省略号），供 setText 使用。"""
        fm = self._goal.fontMetrics()
        avail = max(40, self._goal.width() - 8)
        max_lines = 2
        ell = "…"
        out: list[str] = []
        cur = text
        while cur:
            if fm.horizontalAdvance(cur) <= avail:
                out.append(cur)
                cur = ""
                break
            lo, hi = 1, len(cur)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if fm.horizontalAdvance(cur[:mid]) <= avail:
                    lo = mid
                else:
                    hi = mid - 1
            if len(out) == max_lines - 1:
                # 最后一行的剩余内容放不下 → 省略
                last = cur[:lo].rstrip(" \t。，；、")
                if last and fm.horizontalAdvance(last + ell) <= avail:
                    last += ell
                out.append(last)
                cur = ""
            else:
                out.append(cur[:lo])
                cur = cur[lo:]
            if len(out) >= max_lines and cur:
                break
        return "\n".join(out)

    def _goal_display_text(self) -> str:
        body = self._goal_raw.strip() or "（点击设置目标）"
        return f"目标：{body}"

    def set_goal(self, text: str):
        self._goal_raw = text or ""
        self._goal.setText(self._elide_goal(self._goal_display_text()))
        self._goal.setToolTip(
            f"点击查看 / 编辑目标：\n{self._goal_raw}" if self._goal_raw.strip() else ""
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_goal", None) is not None and getattr(self, "_goal_raw", None) is not None:
            self._goal.setText(self._elide_goal(self._goal_display_text()))

    def clear(self):
        self._title.setText("未选择项目")
        self._status.setText("")
        self.set_goal("")
        self._policy_btn.setText("审核策略")
        self._policy_btn.setEnabled(False)
        self._ai_refine_btn.setEnabled(False)
        self._ai_refine_btn.setText("✨")

    def set_ai_refine_busy(self, busy: bool):
        if busy:
            self._ai_refine_btn.setEnabled(False)
            self._ai_refine_btn.setText("…")
        else:
            self._ai_refine_btn.setEnabled(True)
            self._ai_refine_btn.setText("✨")

    def bind(self, project: Project, *, project_root: Path | None = None):
        c = self.theme.colors
        self._title.setText(project.title)
        # 刷新顶栏时勿打断「AI 更新中」状态
        if self._ai_refine_btn.text() != "…":
            self._ai_refine_btn.setEnabled(True)
            self._ai_refine_btn.setText("✨")
        self._policy_btn.setEnabled(True)
        st = STATUS_LABEL.get(project.status, project.status.value)
        color = c[STATUS_COLOR_KEY.get(project.status, "text_hint")]
        self._status.setText(st)
        self._status.setStyleSheet(f"font-size: 12px; color: {color};")

        self.set_goal(project.goal)

        summary = project.approval.summary()
        self._policy_btn.setText(summary)
        style = f"""
            QPushButton {{
                background: {c["btn_bg"]}; color: {c["danger" if project.approval.skip_high_risk else "text"]};
                border: none; border-radius: 6px; padding: 0 10px; font-size: 12px;
                font-weight: {'600' if project.approval.skip_high_risk else 'normal'};
            }}
            QPushButton:hover {{ background: {c["btn_hover"]}; }}
        """
        self._policy_btn.setStyleSheet(style)
