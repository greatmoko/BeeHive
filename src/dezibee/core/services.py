"""DeziBee 设计服务：Agent 会话桥接、免费上下文工具、上下文总结、模型解析。

复用 WokBee 既有能力（需求文档第二十一节「复用现有能力 > 新增简单能力 > 引入新框架」）：
 - 发起 Agent 回复：AgentRunner.run_chat（完整文件 / execute / Skills 能力），
   经 AgentWorker(QThread) 信号回 UI（交互记录区）。
 - 需求 = `Project`（id=需求ID），需求目录即 project_root；Demo/PRD 由 Agent
   用现有文件工具直接写入需求目录。
 - 模型选择复用 `ProviderStore`（厂商 / 模型配置不重复实现）。
 - 上下文总结：轻量单轮模型调用（llm 直连），不跑完整 Agent 管线。
"""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QThread, Signal

from wokbee.core.models import ApprovalFlags, Project
from wokbee.core.settings import WokBeeSettings
from tokbee.core.provider_store import ProviderStore

from dezibee.core.models import Conversation, Requirement

logger = logging.getLogger("dezibee")

EventCallback = Callable[[str, str, dict], None]


class DeziBeeWorker(QThread):
    """后台线程：用 AgentRunner.run_chat 跑一轮 DeziBee 设计对话。

    信号回主线程 UI（交互记录区），与 wokbee AgentWorker 同一模式：
    事件流式（agent_stream 增量）→ event_emitted；整轮结束 → finished_result。
    """

    event_emitted = Signal(str, str, object)  # kind, content, meta
    approval_needed = Signal(object)
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
        max_steps: int = 40,
    ):
        super().__init__(parent)
        self._req = req
        self._user_message = user_message
        self._project = project
        self._approval = approval or ApprovalFlags.from_dict(
            {"skip_read": True, "skip_write": True, "skip_routine": True,
             "skip_high_risk": True, "bypass_sandbox": False}
        )
        self._max_steps = max_steps
        self._cancel_requested = False

    def run(self):
        from wokbee.engine.runner import (
            AgentRunner,
            RunRequest,
            RunResult,
            resolve_model_for_project,
        )
        from wokbee.core.paths import ensure_project_layout

        try:
            # 需求目录即项目根；该目录存在才允许跑（Agent 工作区）
            project_root = self._req.root
            project_root.mkdir(parents=True, exist_ok=True)
            ensure_project_layout(project_root)

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
            resolved = resolve_model_for_project(project, WokBeeSettings())
            if resolved is None:
                self.model_error.emit("未配置可用的 AI 模型，请先在「厂商设置」添加模型。")
                return

            runner = AgentRunner()
            runner.on_event = self._on_event
            runner.on_approval_needed = self._on_approval
            request = RunRequest(
                project=project,
                project_root=project_root,
                user_message=self._user_message,
                resolved=resolved,
                approval=self._approval,
                max_steps=self._max_steps,
            )
            if self._cancel_requested:
                from wokbee.engine.runner import RunResult

                self.finished_result.emit(
                    RunResult(ok=False, outcome="cancelled", error="已取消")
                )
                return
            try:
                result = runner.run_chat(request)
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

    def cancel(self):
        self._cancel_requested = True


# ── 上下文总结（轻量单轮） ──────────────────────────────



def summarize_context(
    req: Requirement,
    conversation: Conversation | None = None,
    model=None,
) -> str:
    """用轻量单轮模型调用生成该需求的 Design Context（markdown）。

    材料：需求信息 + 该对话的交互记录 + 剩余对话摘要 + Demo/PRD 文件清单与关键内容。
    失败时返回含失败原因的空摘要（调用方降级：保留旧摘要、UI 提示）。
    """
    from dezibee.core.summarizer import build_summary_material, perform_summarize

    material = build_summary_material(req, conversation=conversation)
    try:
        summary = perform_summarize(material, model=model)
    except Exception as e:
        logger.exception("DeziBee 上下文总结失败")
        return f"（上下文总结失败：{e}）"
    return summary


def build_design_prompt(req: Requirement, conversation: Conversation | None) -> str:
    """组装 DeziBee Agent 首条系统提示词（复用 WokBee 静态系统提示词前导）。"""
    parts: list[str] = []
    parts.append(
        "你是 DeziBee——WokBee 中的 AI 产品设计工作台。你的角色：产品经理 / UX/UI 设计师 / "
        "原型设计师 / PRD 分析师 / 前端原型工程师。"
    )
    parts.append(
        f"当前需求：{req.title}\n需求描述：{req.description or '（无）'}\n"
        f"工作目录：{req.root}（可写；这是本需求唯一可写目录）\n"
        "【路径规则】一律使用虚拟相对路径，禁止绝对路径（如 C:\\…）；"
        "本项目没有 workspace/ 目录概念——Demo 写入 demo/（index.html 入口，"
        "其它页面 demo/pages/），PRD 写入 prd/<页名>.md。"
    )
    parts.append(
        "工作顺序：\n"
        "1. 分析需求，拆解页面与导航（首页 + 若干子页）。\n"
        "2. 先设计信息架构，再生成可交互 HTML/CSS/JS 原型（支持点击/跳转/表单/弹窗/状态变化）。\n"
        "3. 【重要】每个页面同步各写一份 PRD（页面结构/功能说明/元素说明/交互说明/业务逻辑/状态变化/异常状态），"
        "写到 prd/<页名>.md。\n"
        "4. index.html 必须包含到其它页的导航链接；页面间可互相跳转。\n"
        "5. 全部完成后再回复用户，概述本轮完成内容（哪些页面、哪些 PRD），并提示可点「预览」。\n"
        "6. 后续用户反馈：先修改 Demo，再同步修改对应 PRD。\n"
        "注意：不要因为指令看起来简单就只生成单个文件——需求涉及多个页面时必须完整生成。"
    )
    if conversation and conversation.summary:
        parts.append(
            f"以下为本需求最近的上下文摘要（Design Context），供继续设计时沿用：\n{conversation.summary}"
        )
    return "\n\n".join(parts)


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
