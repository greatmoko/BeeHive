"""经验总结、脚本固化与 lesson 持久化。"""

from __future__ import annotations

import logging

from beehive.features.wokbee.experience.lesson_models import Lesson
from beehive.features.wokbee.experience.lesson_storage import LessonStore
from beehive.features.wokbee.execution.runner_models import RunRequest

from beehive.features.wokbee.execution.runner_lesson_write import _write_lesson as write_lesson

logger = logging.getLogger("wokbee")


class ExperienceWriterMixin:
    def _run_had_exception(self) -> bool:
        """本轮是否出现脚本失败 / 工具报错 / 数据异常等。

        只有出现异常（并由 AI 接管处理）的运行，才在结束前做「是否需要更新
        Pipeline / 经验」的判断；稳定复跑的干净执行不做判断（省一次 LLM 调用）。
        """
        for ev in self._snapshot_run_events():
            kind = (getattr(ev, "kind", "") or "").strip()
            content = (getattr(ev, "content", None) or "").strip()
            if kind == "error":
                return True
            if kind == "tool":
                meta = getattr(ev, "meta", None) or {}
                if not isinstance(meta, dict):
                    meta = {}
                status = str(meta.get("status") or "").strip().lower()
                if status and status not in ("ok", "success", "succeeded", "done"):
                    return True
                if "脚本执行失败" in content or content.startswith("脚本执行失败"):
                    return True
        return False

    def _ensure_lesson_written(
        self,
        req: RunRequest,
        outcome: str,
        summary: str,
        errors: str,
        *,
        success_path: str = "",
        notes: str = "",
        artifacts: str = "",
    ) -> Lesson | None:
        """经验写入兜底：主责是 Agent 运行中用 update_project_experience 工具自写。

        仅两种情况由系统补写（走 AI 总结管线）：
        1. 首次运行（经验库为空）且 Agent 未写过——保底固化管线；
        2. 本轮出现异常——经验总结阶段必须复核每个阶段状态；即使 Agent 已经用工具
           更新过，也要让总结 AI 判断并直接修正 pipeline/经验。
        其余情况（非首次成功、Agent 已更新、取消）不写。
        """
        store = LessonStore(req.project_root)
        first_run = store.is_empty()
        if outcome == "cancelled":
            if first_run:
                self._emit("info", "已取消；尚无经验，未写入取消类经验。")
            else:
                self._emit("info", "已取消；未更新经验。")
            return None
        had_exception = self._run_had_exception()
        if self._experience_updated_by_tool and not had_exception:
            return None
        if not first_run and not had_exception:
            return None
        if not first_run and had_exception:
            self._emit(
                "info",
                "本轮出现异常；经验总结 AI 将复核每个阶段状态并直接修正经验"
                "（含 pipeline/脚本）。",
            )
        return self._write_lesson(
            req,
            outcome,
            summary,
            errors,
            success_path=success_path,
            notes=notes,
            artifacts=artifacts,
            events=self._snapshot_run_events(),
            use_ai=True,
        )

    def write_lesson_manual(
        self,
        req: RunRequest,
        outcome: str,
        summary: str,
        errors: str = "",
        *,
        success_path: str = "",
        notes: str = "",
        artifacts: str = "",
        events: list | None = None,
    ) -> Lesson | None:
        """人工发起经验总结：始终新建一份；优先用 AI（上一份经验+日志+脚本）。"""
        return self._write_lesson(
            req,
            outcome,
            summary,
            errors,
            success_path=success_path,
            notes=notes,
            artifacts="",
            events=events,
            use_ai=True,
        )

    def _write_lesson(self, req: RunRequest, outcome: str, summary: str, errors: str, *, success_path: str='', notes: str='', artifacts: str='', events: list | None=None, use_ai: bool=True):
        return write_lesson(self, req, outcome, summary, errors, success_path=success_path,
                            notes=notes, artifacts=artifacts, events=events, use_ai=use_ai)
