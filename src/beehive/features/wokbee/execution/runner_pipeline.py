"""运行入口、恢复与成功/失败收尾；单轮状态显式归属。"""
from __future__ import annotations
from beehive.system.prompts import PIPELINE_RESUME_PROMPT
from beehive.features.wokbee.execution.runner_models import RunResult, StepLimitExceeded
from beehive.features.wokbee.execution.runner_sessions import reset_run_state as _reset_run_state
from beehive.system.agent.message_content import _attachment_content
from beehive.system.agent.message_content import _extract_text
from beehive.features.wokbee.execution.runner_support import _format_engine_error
from beehive.features.wokbee.execution.runner_support import build_success_path_from_messages
from beehive.features.wokbee.execution.runner_modes import mode_policy
from beehive.features.wokbee.execution.runner_pipeline_state import PipelineState
import logging

logger = logging.getLogger("wokbee")


def run_pipeline(self, req: RunRequest, *, resume: bool = False) -> RunResult:
    run_state = PipelineState()
    policy = mode_policy(req.runner_mode or "run")
    if not policy.pipeline:
        return self._run_conversation(req, policy)
    self._event_log.reset()
    self._context_injected = False
    self._experience_updated_by_tool = False
    thread_id = f"wokbee-{req.project.id}"
    self._configure_step_budget(req)
    run_state.config = self._graph_config(thread_id, req)
    run_state.seen_msg_ids: set[str] = set()

    run_state.base_message = (
        req.user_message.strip()
        or req.project.goal
        or "请根据项目目标推进工作。"
    )
    run_state.base_content = _attachment_content(run_state.base_message, req.attachments)

    try:
        # 非 resume 必须清空 checkpoint，否则会继承上次空 AIMessage / 半截计划而秒退
        if not resume:
            _reset_run_state(req.project.id)
        run_state.agent = self.build_agent(req, mode=policy.name)
    except Exception as e:
        logger.exception("创建 Agent 失败")
        return RunResult(ok=False, outcome="failed", error=str(e))

    self._emit(
        "agent",
        f"引擎已启动（Deep Agents + 联网工具）。"
        f"模型：{req.resolved.provider_name}/{req.resolved.model_id}\n"
        f"策略：{req.approval.summary()}；Agent 工作目录：{req.project_root}\n"
        "可用：web_search / http_get / http_request / 文件工具 / execute\n"
        "目录约定：文件工具虚拟路径 / 即项目根；execute 用真实路径（工作目录为项目根）。"
        "uploads/ 读写；output/ 交付物；memory/ 记忆（events.jsonl 由系统维护）；"
        "archives/ 禁止访问；临时脚本与中间材料放项目根目录。\n"
        "执行策略：AI 主导——第一时间查看 memory/ 是否有最新经验文件："
        "非首次运行则按经验记载的成功路径调取并运行 memory/ 下的可复用脚本，再推进剩余任务；"
        "首次运行则直接推进目标；收尾固化已验证的流程与脚本供下次复用。",
        {"phase": "hint"},
    )

    run_state.final_text = ""
    run_state.trajectory_messages: list = []

    try:
        if resume:
            early = self._run_agent_turn(
                run_state.agent,
                run_state.config,
                run_state.seen_msg_ids,
                req,
                payload={
                    "messages": [
                        {
                            "role": "user",
                            "content": PIPELINE_RESUME_PROMPT,
                        }
                    ]
                },
                first=True,
            )
        else:
            # ── AI 主导：读取经验与管线参考，由 AI 决定脚本执行 ──
            early = self._run_agent_turn(
                run_state.agent,
                run_state.config,
                run_state.seen_msg_ids,
                req,
                payload={
                    "messages": [
                        {"role": "user", "content": run_state.base_content}
                    ]
                },
                first=True,
            )
        if early:
            self._finalize_early_failure(req, early, run_state.agent, run_state.config)
            return early

        # 收尾：再取一次最终文本（不重复刷时间线）
        try:
            state = run_state.agent.get_state(run_state.config)
            values = getattr(state, "values", None) or {}
            messages = values.get("messages") if isinstance(values, dict) else None
            if messages:
                run_state.trajectory_messages = list(messages)
                run_state.final_text = _extract_text(run_state.trajectory_messages) or run_state.final_text
        except Exception:
            pass

        if self._cancel.is_set():
            return self._cancelled_result(req, True)

        success_path = build_success_path_from_messages(run_state.trajectory_messages)
        lesson = self._ensure_lesson_written(
            req,
            "success",
            run_state.final_text[:800] or "任务执行完成",
            "",
            success_path=success_path,
        )
        self._emit("info", "运行结束：成功")
        return RunResult(
            ok=True,
            outcome="success",
            final_text=run_state.final_text,
            lesson_id=lesson.id if lesson else "",
        )

    except StepLimitExceeded as e:
        logger.warning("Agent 达到 max_steps 硬上限：%s", e)
        err = str(e)
        self._emit("error", err, {"outcome": "incomplete", "max_steps": e.limit})
        fail_path = ""
        try:
            state = run_state.agent.get_state(run_state.config)
            values = getattr(state, "values", None) or {}
            messages = values.get("messages") if isinstance(values, dict) else None
            if messages:
                fail_path = build_success_path_from_messages(list(messages))
        except Exception:
            pass
        lesson = self._ensure_lesson_written(
            req,
            "partial",
            "执行未完成：已达到 max_steps 硬上限",
            err,
            success_path=fail_path,
        )
        return RunResult(
            ok=False,
            outcome="incomplete",
            error=err,
            lesson_id=lesson.id if lesson else "",
        )
    except Exception as e:
        logger.exception("Agent 运行失败")
        err = _format_engine_error(e)
        self._emit("error", f"执行失败：{err}")
        fail_path = ""
        try:
            state = run_state.agent.get_state(run_state.config)
            values = getattr(state, "values", None) or {}
            messages = values.get("messages") if isinstance(values, dict) else None
            if messages:
                fail_path = build_success_path_from_messages(list(messages))
        except Exception:
            pass
        lesson = self._ensure_lesson_written(
            req,
            "failed",
            str(e)[:500],
            str(e),
            success_path=fail_path,
        )
        return RunResult(
            ok=False,
            outcome="failed",
            error=err,
            lesson_id=lesson.id if lesson else "",
        )
