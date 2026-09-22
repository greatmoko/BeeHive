"""WokBee 工作区：侧栏项目列表、项目要素、后台 worker、与三段式整体工作区。

聚合了四种自有定义：侧栏/项目列表、项目要素、后台 worker（上下文压缩 / AI 改名）、
以及把时间线 + 操作栏 + 侧栏拼成一体的 `_ProjectWorkspace`。时间线与操作栏本身
分别位于 `timeline.py` / `action_bar.py`。
"""

from __future__ import annotations

from sysprompt import project_metadata_system_prompt

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tokbee.core import context_manager as ctxman
from tokbee.core.ai_client import AIClient
from tokbee.core.provider_store import ProviderStore, ResolvedModel
from tokbee.core.session_settings import SessionSettings, ProviderOptions
from tokbee.ui.styles.system import make_context_menu
from tokbee.ui.styles.theme import Theme

from wokbee.core.context_usage import (
    estimate_project_usage,
    load_context_state,
    plan_project_compaction,
    save_context_state,
)
from wokbee.core.models import MAX_PROJECT_TITLE_LEN, Project, ProjectEvent, ProjectStatus
from wokbee.core.paths import deliverables_dir, list_deliverable_names, uploads_dir
from wokbee.core.project_store import MAX_ARCHIVES, ProjectStore, TRASH_RETENTION_DAYS
from wokbee.ui.action_bar import _ActionBar
from wokbee.ui.ask_user_dialog import AskUserDialog
from wokbee.ui.dialogs import (
    _ask_multiline,
    _ask_text,
    _confirm,
    _default_project_title,
    _prompt_approval_flags,
    open_path as _open_in_explorer,
    tip as _tip,
)
from wokbee.ui.timeline import INITIAL_RENDER
from wokbee.ui.web_chat import _WebChat


logger = logging.getLogger("wokbee")


def _model_request_settings(model: ResolvedModel) -> SessionSettings:
    """将厂商-模型配置转换为 TokBee 客户端请求参数。"""
    return SessionSettings(
        temperature=model.temperature,
        top_p=model.top_p,
        max_tokens=model.max_tokens,
        stream=model.stream,
        provider_options=ProviderOptions(
            reasoning_adapter=model.reasoning_adapter,
            reasoning_effort=model.reasoning_effort,
            reasoning_enabled=model.reasoning_enabled,
        ),
    )


STATUS_LABEL = {
    ProjectStatus.IDLE: "空闲",
    ProjectStatus.RUNNING: "运行中",
    ProjectStatus.AWAITING_APPROVAL: "待审批",
    ProjectStatus.FAILED: "失败",
    ProjectStatus.DONE: "完成",
}

STATUS_COLOR_KEY = {
    ProjectStatus.IDLE: "text_hint",
    ProjectStatus.RUNNING: "accent",
    ProjectStatus.AWAITING_APPROVAL: "warning",
    ProjectStatus.FAILED: "danger",
    ProjectStatus.DONE: "success",
}

# 新建项目时，名称默认取目标前 N 个字（也是名称硬上限）
TITLE_FROM_GOAL_LEN = MAX_PROJECT_TITLE_LEN


class _CompactWorker(QThread):
    """后台生成上下文摘要，写入 compaction point。"""

    compact_done = Signal(str, int, int)  # summary, boundary_index, pin_end
    failed = Signal(str)

    def __init__(
        self,
        client: AIClient | None,
        to_compact: list[dict],
        previous_summary: str,
        new_boundary: int,
        parent=None,
        *,
        pin_end: int = 0,
        model: ResolvedModel | None = None,
    ):
        super().__init__(parent)
        self._client = client
        self._model = model
        self._to_compact = to_compact
        self._previous_summary = previous_summary
        self._new_boundary = new_boundary
        self._pin_end = int(pin_end or 0)
        self._cancelled = False
        if client is not None:
            client.cancel_check = lambda: self._cancelled

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self._cancelled:
            return
        summary = ""
        if self._client is not None and self._model is not None:
            try:
                msgs = ctxman.build_summary_prompt_messages(
                    self._to_compact, self._previous_summary,
                )
                resp = self._client.chat(msgs, settings=_model_request_settings(self._model))
                summary = (resp.content or "").strip()
                if not summary and resp.reasoning_content:
                    summary = resp.reasoning_content.strip()
            except Exception:
                summary = ""
        if self._cancelled:
            return
        if not summary:
            summary = ctxman.mechanical_summary(
                self._to_compact, self._previous_summary,
            )
        if self._cancelled:
            return
        if not summary.strip():
            self.failed.emit("无法生成摘要")
            return
        self.compact_done.emit(summary, self._new_boundary, self._pin_end)


class _RefineMetaWorker(QThread):
    """后台调用 AI，根据最近交互记录生成新的项目名称与目标。"""

    finished_ok = Signal(str, str)  # title, goal
    failed = Signal(str)

    def __init__(
        self,
        settings,
        project,
        *,
        current_title: str,
        current_goal: str,
        recent_context: str,
        max_title_len: int,
        parent=None,
    ):
        super().__init__(parent)
        self._settings = settings
        self._project = project
        self._current_title = current_title
        self._current_goal = current_goal
        self._recent_context = recent_context
        self._max_title_len = max_title_len
        self._cancelled = False
        self._client = None
        self._resolved = None

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self._cancelled:
            return
        # 模型解析 + AIClient 构造放进 worker 线程，避免 UI 线程 import 重型引擎。
        from wokbee.engine import ensure_engine_warm

        ensure_engine_warm()
        from wokbee.engine.runner import resolve_model_for_project

        try:
            resolved = resolve_model_for_project(self._project, self._settings)
        except Exception as e:
            if not self._cancelled:
                self.failed.emit(str(e))
            return
        if not (resolved.api_key and resolved.api_host and resolved.model_id):
            self.failed.emit("请先在「厂商设置」配置可用模型。")
            return
        client = AIClient(
            resolved.api_host,
            resolved.api_key,
            resolved.model_id,
            family=resolved.family,
            protocol=resolved.api_protocol,
        )
        self._resolved = resolved
        self._client = client
        client.cancel_check = lambda: self._cancelled
        system = project_metadata_system_prompt(self._max_title_len)
        user = (
            f"当前名称：{self._current_title or '（空）'}\n"
            f"当前目标：{self._current_goal or '（空）'}\n\n"
            f"最近交互记录：\n{self._recent_context or '（无）'}"
        )
        try:
            resp = self._client.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                settings=_model_request_settings(resolved),
            )
            raw = (resp.content or "").strip() or (resp.reasoning_content or "").strip()
        except Exception as e:
            if not self._cancelled:
                self.failed.emit(str(e))
            return
        if self._cancelled:
            return

        title, goal = self._parse(raw)
        if not title and not goal:
            self.failed.emit("模型未返回可用的名称/目标")
            return
        if title and len(title) > self._max_title_len:
            title = title[: self._max_title_len]
        self.finished_ok.emit(title or self._current_title, goal or self._current_goal)

    @staticmethod
    def _parse(raw: str) -> tuple[str, str]:
        text = (raw or "").strip()
        if not text:
            return "", ""
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fence:
            text = fence.group(1).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{[\s\S]*\}", text)
            if not m:
                return "", ""
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return "", ""
        if not isinstance(data, dict):
            return "", ""
        title = str(data.get("title") or "").replace("\x00", "").strip()
        goal = str(data.get("goal") or "").replace("\x00", "").strip()
        return title, goal
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
        self._ai_refine_btn.setFixedSize(32, 28)
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
        self._policy_btn.setFixedHeight(28)
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
