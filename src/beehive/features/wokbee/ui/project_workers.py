"""项目摘要与名称目标优化的后台线程。"""
from __future__ import annotations
from beehive.system.prompts import project_metadata_system_prompt
import json
from beehive.system.providers.model_settings import settings_for_model
import re
from PySide6.QtCore import QThread, Signal
from beehive.system.memory import context as ctxman
from beehive.system.providers.ai_client import AIClient
from beehive.system.providers.provider_models import ResolvedModel
class _CompactWorker(QThread):
    """后台生成上下文摘要，写入 compaction point。"""

    compact_done = Signal(str, int, int)  # 摘要、边界序号、固定前缀末尾
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
                resp = self._client.chat(msgs, settings=settings_for_model(self._model))
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

    finished_ok = Signal(str, str)  # 名称、目标
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
        from beehive.features.wokbee.execution import ensure_engine_warm

        ensure_engine_warm()
        from beehive.features.wokbee.execution.runner_assembly import resolve_model_for_project

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
                settings=settings_for_model(resolved),
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
