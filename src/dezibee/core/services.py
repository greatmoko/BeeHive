"""DeziBee 设计服务：Agent 会话桥接、模型解析。

复用 WokBee 既有能力（需求文档第二十一节「复用现有能力 > 新增简单能力 > 引入新框架」）：
 - 发起 Agent 回复：AgentRunner.run_design（完整文件 / execute / Skills 能力），
   经 AgentWorker(QThread) 信号回 UI（交互记录区）。
 - 需求 = `Project`（id=需求ID），需求目录即 project_root；Demo/PRD 由 Agent
   用现有文件工具直接写入需求目录。
 - 模型选择复用 `ProviderStore`（厂商 / 模型配置不重复实现）。
"""

from __future__ import annotations

from sysprompt import build_design_prompt

import logging
from typing import Callable

from PySide6.QtCore import QThread, Signal

from wokbee.core.models import ApprovalFlags, Project
from wokbee.core.settings import WokBeeSettings
from tokbee.core.provider_store import ProviderStore

from dezibee.core.models import Requirement

logger = logging.getLogger("dezibee")

EventCallback = Callable[[str, str, dict], None]


class DeziBeeWorker(QThread):
    """后台线程：用 AgentRunner.run_design 跑一轮 DeziBee 设计对话。

    信号回主线程 UI（交互记录区），与 wokbee AgentWorker 同一模式：
    事件流式（agent_stream 增量）→ event_emitted；整轮结束 → finished_result。
    """

    event_emitted = Signal(str, str, object)  # kind, content, meta
    approval_needed = Signal(object)
    ask_user_needed = Signal(object)  # dict payload（澄清意图弹窗）
    finished_result = Signal(object)
    model_error = Signal(str)

    def __init__(
        self,
        req: Requirement,
        user_message: str,
        parent=None,
        *,
        project: Project | None = None,
        approval: ApprovalFlags | None = None,
        max_steps: int | None = None,
        attachments: list[dict] | None = None,
        conversation_id: str = "",
        settings: WokBeeSettings | None = None,
    ):
        super().__init__(parent)
        self._req = req
        self._user_message = user_message
        self._project = project
        self._attachments = list(attachments or [])
        self._settings = settings or WokBeeSettings()
        self._approval = (approval if approval is not None else self._settings.dezibee_approval).copy()
        self._max_steps = self._settings.chat_max_steps if max_steps is None else int(max_steps)
        self._conversation_id = str(conversation_id or "")
        self._cancel_requested = False
        self._runner = None  # run() 中创建；cancel() 需要借此中断进行中的一轮

    def run(self):
        from wokbee.core.project_run_queue import project_run_slot

        # 与 WokBee/Gateway/AutoBee 共用运行槽；不同需求仍可并行。
        with project_run_slot(self._req.id):
            self._run_in_slot()

    def _run_in_slot(self):
        if self._cancel_requested:
            from wokbee.engine.runner_models import RunResult

            self.finished_result.emit(RunResult(ok=False, outcome="cancelled", error="已取消"))
            return
        from wokbee.engine.runner import (
            AgentRunner,
            RunRequest,
            RunResult,
            resolve_model_for_project,
        )

        try:
            # 需求目录即项目根；该目录存在才允许跑（Agent 工作区）
            project_root = self._req.root
            project_root.mkdir(parents=True, exist_ok=True)

            # 用需求绑定模型；未绑定时用全局默认/第一可用
            provider_store = ProviderStore()
            project = self._project or Project(
                id=self._req.id,
                title=self._req.title,
                goal=self._req.description,
                provider=self._req.provider,
                model_id=self._req.model_id,
                approval=self._approval,
            )
            resolved = resolve_model_for_project(project, self._settings)
            if resolved is None:
                self.model_error.emit("未配置可用的 AI 模型，请先在「厂商设置」添加模型。")
                return

            runner = AgentRunner(self._settings)
            self._runner = runner
            runner.on_event = self._on_event
            runner.on_approval_needed = self._on_approval
            runner.on_ask_user_needed = self._on_ask_user
            request = RunRequest(
                project=project,
                project_root=project_root,
                user_message=self._user_message,
                resolved=resolved,
                approval=self._approval,
                max_steps=self._max_steps,
                attachments=self._attachments,
                runner_mode="design",
                chat_thread_id=self._conversation_id,
                design_context=build_design_prompt(self._req),
            )
            if self._cancel_requested:
                from wokbee.engine.runner import RunResult

                self.finished_result.emit(
                    RunResult(ok=False, outcome="cancelled", error="已取消")
                )
                return
            try:
                result = runner.run_design(request)
            except Exception as e:
                logger.exception("DeziBee 对话失败")
                result = RunResult(ok=False, outcome="failed", error=str(e))
            self.finished_result.emit(result)
        except Exception as e:
            from wokbee.engine.runner import RunResult

            logger.exception("DeziBee 会话初始化失败")
            self.finished_result.emit(
                RunResult(ok=False, outcome="failed", error=str(e))
            )

    def _on_event(self, kind: str, content: str, meta: dict):
        self.event_emitted.emit(kind, content, meta)

    def _on_approval(self, pending: list):
        self.approval_needed.emit(pending)

    def resolve_approval(self, decisions: list[dict]):
        runner = self._runner
        if runner is not None:
            runner.resolve_approval(decisions)

    def _on_ask_user(self, payload: dict):
        self.ask_user_needed.emit(payload)

    def resolve_ask_user(self, answers: dict):
        """UI 弹窗收集答案后回传后台 runner，唤醒等待中的澄清。"""
        runner = self._runner
        if runner is not None:
            runner.resolve_ask_user(answers)

    def cancel(self):
        """请求暂停：中断进行中的一轮对话（含正在执行的命令/等待审批）。"""
        self._cancel_requested = True
        runner = self._runner
        if runner is not None:
            try:
                runner.request_cancel()
            except Exception:
                logger.exception("请求取消 Agent 运行失败")


# ── 模型解析 ────────────────────────────────────────────

def resolve_req_model(req: Requirement) -> object | None:
    """解析需求绑定模型（ProviderStore.resolve）；返回 ChatOpenAI 或 None。"""
    from wokbee.engine.model_factory import build_chat_model

    store = ProviderStore()
    if req.provider and req.model_id:
        resolved = store.resolve(req.provider, req.model_id)
    else:
        resolved = store.first_resolved()
    if resolved is None:
        return None
    try:
        return build_chat_model(resolved)
    except Exception:
        logger.exception("构造模型失败")
        return None


def model_options() -> list[tuple[str, tuple[str, str]]]:
    """可选模型列表 [(label, (provider_id, model_id))]。"""
    try:
        models = ProviderStore().list_selectable_models()
    except Exception:
        logger.exception("读取可选模型失败")
        return []
    return [
        (f"{m.provider_name} / {m.model_id}", (m.provider_id, m.model_id))
        for m in models
    ]


def current_model_label(pair: tuple[str, str]) -> str:
    """当前模型展示名：（默认模型）或「厂商 / model_id」；未知组合回退原始 id。"""
    provider_id, model_id = (str(p) for p in pair)
    if not model_id:
        return "（默认模型）"
    try:
        models = ProviderStore().list_selectable_models()
    except Exception:
        logger.exception("读取当前模型失败")
        return model_id
    for m in models:
        if m.provider_id == provider_id and m.model_id == model_id:
            return f"{m.provider_name} / {m.model_id}"
    return model_id
