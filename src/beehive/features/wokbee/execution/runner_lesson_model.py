"""经验模型输入调用、结果预览和规则回退。"""
from __future__ import annotations
import logging
from beehive.features.wokbee.experience.lesson_ai import summarize_lesson_with_ai
from beehive.system.agent.model_factory import build_chat_model

logger = logging.getLogger("wokbee")

def summarize_run(self, req, outcome, previous_text, run_log, summary, scripts_ctx, env, use_ai):
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

    return ai_fields
