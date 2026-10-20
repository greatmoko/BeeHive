"""经验上下文收集、结果合并、脚本固化和持久化协调。"""
from __future__ import annotations
import logging
from beehive.features.wokbee.experience.lesson_models import Lesson
from beehive.features.wokbee.experience.lesson_storage import LessonStore
from beehive.features.wokbee.experience.lesson_digest import build_lesson_digest
from beehive.features.wokbee.experience.lesson_context import collect_scripts_context
from beehive.features.wokbee.execution.runner_experience import fallback_notes, fallback_success_path, load_latest_round_events
from beehive.features.wokbee.execution.runner_models import RunRequest
from beehive.system.runtime.runtime_env_format import build_runtime_env_block
from beehive.features.wokbee.experience.lesson_summary import solidify_lesson_scripts
from beehive.features.wokbee.execution.runner_lesson_model import summarize_run
from beehive.features.wokbee.execution.runner_lesson_references import snapshot_references

logger = logging.getLogger("wokbee")

def _write_lesson(
    self,
    req: RunRequest,
    outcome: str,
    summary: str,
    errors: str,
    *,
    success_path: str = "",
    notes: str = "",
    artifacts: str = "",
    events: list | None = None,
    use_ai: bool = True,
) -> Lesson | None:
    try:
        store = LessonStore(req.project_root)
        # 固化用的「原始工具轨迹」：必须保留，不能被 AI 散文覆盖
        trace_for_scripts = (success_path or "").strip()

        # 事件优先：调用方传入 > 本轮内存缓冲 > 磁盘 events.jsonl
        if events is None:
            mem_events = self._snapshot_run_events()
            events = mem_events if mem_events else None
        if not events:
            try:
                events = load_latest_round_events(req.project_root)
            except OSError:
                events = []
        # 内存缓冲有工具调用时并入（补磁盘竞态缺口）
        mem_events = self._snapshot_run_events()
        if mem_events:
            events = list(events or []) + [
                e
                for e in mem_events
                if getattr(e, "kind", "") == "tool"
                and (getattr(e, "meta", None) or {}).get("phase") == "call"
            ]

        previous_text = store.read_latest_text(max_chars=8000)
        run_log = build_lesson_digest(events)
        scripts_ctx = collect_scripts_context(req.project_root)
        env = build_runtime_env_block(
            project_root=str(req.project_root),
            model=f"{req.resolved.provider_name}/{req.resolved.model_id}",
            policy=req.approval.summary(),
            settings=self.settings,
        )

        self._emit(
            "info",
            "准备经验总结上下文：\n"
            f"- 上一份经验：{len(previous_text or '')} 字\n"
            f"- 运行日志：{len(run_log or '')} 字"
            f"（事件约 {len(events or [])} 条）\n"
            f"- 脚本：{len(scripts_ctx or '')} 字",
        )

        ai_fields = summarize_run(self, req, outcome, previous_text, run_log, summary, scripts_ctx, env, use_ai)

        # 合并：AI 优先，否则用调用方/规则回退；绝不写入产物
        summary_f = (ai_fields.get("summary") or summary or "").strip()
        path_f = (ai_fields.get("success_path") or success_path or "").strip()
        notes_f = (ai_fields.get("notes") or notes or "").strip()

        if not path_f:
            path_f = fallback_success_path(outcome, errors, summary_f)

        if not notes_f:
            notes_f = fallback_notes(errors)

        if not summary_f:
            summary_f = "本轮流程经验（方法向，不含结果/产物）。"

        lesson = Lesson(
            project_id=req.project.id,
            goal=req.project.goal or req.user_message,
            outcome=outcome,
            summary=summary_f,
            success_path=path_f,
            notes=notes_f,
            errors=errors,
            model=f"{req.resolved.provider_name}/{req.resolved.model_id}",
            policy=req.approval.summary(),
        )

        # 固化与工具直写共用；失败交给外层处理，不保存不一致的经验。
        solidify_path = "\n".join(x for x in (trace_for_scripts, path_f) if x)
        ai_written_count = solidify_lesson_scripts(
            req.project_root,
            lesson,
            success_path=solidify_path,
            events=events,
            script_files=ai_fields.get("script_files") or [],
        )
        total_scripts = len(lesson.scripts)
        if total_scripts:
            self._emit(
                "info",
                f"已固化可复用脚本 {total_scripts} 个到 memory/"
                f"（其中 AI 手写 {ai_written_count} 个，均通过存在性校验）；"
                f"下次运行由 AI 读取最新经验后按成功路径调取执行。",
                {"scripts": lesson.scripts},
            )
        else:
            self._emit(
                "info",
                "本轮未识别到可固化脚本，且 AI 未手写 script_files；"
                "故 memory/ 无新脚本。下次若有 execute/.py/.bat 或 AI 手写，"
                "总结时会写入。",
            )

        snapshot_references(self, req, lesson, ai_fields)

        # 注意：总结时不清理、不删除任何已有脚本（memory/ 全部保留）

        path = store.save(lesson)
        try:
            rel = path.relative_to(req.project_root).as_posix()
        except ValueError:
            rel = str(path)
        self._emit(
            "lesson",
            f"已新建经验：{rel}\n"
            f"（多份并存，运行只加载最新；内容不含结果/产物）",
            {"lesson_id": lesson.id, "path": str(path)},
        )
        return lesson
    except Exception as exc:
        logger.exception("写入 lesson 失败")
        self._emit("error", f"写入项目经验失败：{exc}")
        return None
