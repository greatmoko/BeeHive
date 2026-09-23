"""经验总结、脚本固化与 lesson 持久化。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from wokbee.engine.lessons import (
    Lesson,
    LessonStore,
    build_lesson_digest,
    collect_scripts_context,
    summarize_lesson_with_ai,
)
from wokbee.engine.model_factory import build_chat_model
from wokbee.engine.runner_experience import fallback_notes, fallback_success_path, load_latest_round_events
from wokbee.engine.runner_models import RunRequest, RunResult
from wokbee.engine.runtime_env import build_runtime_env_block
from wokbee.engine.lesson_summary import solidify_lesson_pipeline

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
                f"- 脚本/pipeline：{len(scripts_ctx or '')} 字\n"
                f"- 阶段状态：{len(self._phase_states)} 条",
            )

            ai_fields: dict[str, str] = {}
            if use_ai:
                try:
                    # ResolvedModel 才调模型；占位对象跳过
                    if getattr(req.resolved, "api_key", None) and getattr(
                        req.resolved, "api_host", None
                    ):
                        model_label = (
                            f"{req.resolved.provider_name}/{req.resolved.model_id}"
                        )
                        self._emit(
                            "info",
                            f"正在调用 AI 总结经验…\n"
                            f"模型：{model_label}\n"
                            f"输入：上一份经验 + 运行日志 + 脚本",
                        )
                        chat = build_chat_model(
                            req.resolved,
                            timeout=self.settings.model_timeout_seconds,
                        )
                        ai_fields = summarize_lesson_with_ai(
                            model=chat,
                            goal=req.project.goal or req.user_message,
                            outcome=outcome,
                            previous_experience=previous_text,
                            run_log=run_log or summary,
                            scripts_context=scripts_ctx,
                            environment_hint=env,
                            phase_states=json.dumps(
                                self._phase_states,
                                ensure_ascii=False,
                            ),
                        )
                        # 结束后再展示完整结果（生成过程不刷进度气泡）
                        preview_parts = []
                        if ai_fields.get("summary"):
                            preview_parts.append(
                                f"**摘要**\n{ai_fields['summary'][:800]}"
                            )
                        if ai_fields.get("success_path"):
                            preview_parts.append(
                                f"**成功实现路径**\n{ai_fields['success_path'][:1200]}"
                            )
                        if ai_fields.get("notes"):
                            preview_parts.append(
                                f"**注意事项**\n{ai_fields['notes'][:600]}"
                            )
                        ai_scripts = ai_fields.get("script_files") or []
                        if ai_scripts:
                            names = [
                                str(x.get("filename") or "?")
                                for x in ai_scripts
                                if isinstance(x, dict)
                            ]
                            preview_parts.append(
                                f"**AI 手写脚本**\n" + "、".join(names[:12])
                            )
                        if preview_parts:
                            self._emit(
                                "agent",
                                "【AI 经验总结结果】\n\n" + "\n\n".join(preview_parts),
                                {"phase": "lesson"},
                            )
                        self._emit("info", "AI 经验总结完成，正在固化经验、管线与脚本…")
                    else:
                        self._emit("info", "无可用模型密钥，改用规则回退总结经验")
                except Exception as e:
                    logger.exception("AI 总结经验失败，回退规则总结")
                    self._emit("info", f"AI 总结失败，改用规则回退：{e}")

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
            applied_order, ai_written_count = solidify_lesson_pipeline(
                req.project_root,
                lesson,
                success_path=solidify_path,
                events=events,
                script_files=ai_fields.get("script_files") or [],
                pipeline_steps=ai_fields.get("pipeline_steps") or [],
            )
            total_scripts = len(lesson.scripts)
            if total_scripts or applied_order:
                order_note = (
                    "（已采用 AI 给出的 pipeline_steps：script+ai 混合）"
                    if applied_order
                    else "（自动固化真实执行顺序：确定性脚本步骤）"
                )
                self._emit(
                    "info",
                    f"已按成功路径写入有序步骤到 scripts/pipeline.json"
                    f"（脚本 {total_scripts} 个{order_note}；"
                    f"其中 AI 手写脚本 {ai_written_count} 个）；"
                    f"下次按 steps 一路执行（script 自动执行；"
                    f"ai 步骤执行固定业务任务按需耗 Token；仅脚本报错/数据异常时才异常接管）；"
                    f"scripts/ 不参与归档",
                    {"scripts": lesson.scripts},
                )
            elif not total_scripts:
                self._emit(
                    "info",
                    "本轮未识别到可固化脚本，且 AI 未手写 script_files；"
                    "故 scripts/ 无新脚本。下次若有 execute/.py/.bat 或 AI 手写，"
                    "总结时会写入。",
                )

            # 保存本次用到的 Skills 快照与参考材料到 uploads/references/（归档不清理）
            try:
                from wokbee.core.references import (
                    snapshot_used_skills,
                    write_reference_manifest,
                )

                used_skills: list[str] = []
                mats: list[dict] = []
                if isinstance(ai_fields, dict):
                    raw_skills = ai_fields.get("used_skills")
                    if isinstance(raw_skills, list):
                        used_skills = [str(s) for s in raw_skills if str(s).strip()]
                    raw_mats = ai_fields.get("reference_materials")
                    if isinstance(raw_mats, list):
                        mats = [
                            m
                            for m in raw_mats
                            if isinstance(m, dict)
                            and (str(m.get("path") or "").strip() or str(m.get("note") or "").strip())
                        ]
                written = snapshot_used_skills(
                    req.project_root,
                    used_skills,
                )
                manifest_path = write_reference_manifest(
                    req.project_root,
                    used_skills=used_skills,
                    materials=mats,
                    goal=lesson.goal or "",
                )
                snap_msg = f"已保存 {len(written)} 个 Skill 快照到 uploads/references/skills/"
                if manifest_path:
                    try:
                        mrel = manifest_path.relative_to(req.project_root).as_posix()
                    except ValueError:
                        mrel = str(manifest_path)
                    snap_msg += f"，并登记 {mrel}"
                if written or manifest_path:
                    self._emit("info", snap_msg + "（uploads/references/ 不会被归档）")
            except Exception:
                logger.exception("保存参考材料失败（经验仍会写入）")

            # 注意：总结时不清理、不删除任何已有脚本（scripts/ 全部保留）

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
