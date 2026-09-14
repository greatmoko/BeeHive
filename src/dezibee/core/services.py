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
        max_steps: int = 40,
        attachments: list[dict] | None = None,
    ):
        super().__init__(parent)
        self._req = req
        self._user_message = user_message
        self._project = project
        self._attachments = list(attachments or [])
        self._approval = approval or ApprovalFlags.from_dict(
            {"skip_read": True, "skip_write": True, "skip_routine": True,
             "skip_high_risk": True, "bypass_sandbox": False}
        )
        self._max_steps = max_steps
        self._cancel_requested = False
        self._runner = None  # run() 中创建；cancel() 需要借此中断进行中的一轮

    def run(self):
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
            resolved = resolve_model_for_project(project, WokBeeSettings())
            if resolved is None:
                self.model_error.emit("未配置可用的 AI 模型，请先在「厂商设置」添加模型。")
                return

            runner = AgentRunner()
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


def _shell_directive(req: Requirement) -> str:
    """设备外壳/实际尺寸指令：默认壳取需求设置，AI 可按卡片覆盖。"""
    shell = (getattr(req, "device_shell", "") or "").strip().lower()
    known = {"phone", "tablet", "browser"}
    if shell not in known:
        # 未设置时按需求描述推断；推断不了就用手机（最常见）
        desc = (req.description or "") + (req.title or "")
        if any(k in desc for k in ("桌面", "网页", "web", "后台", "管理", "Web", "PC", "pc")):
            shell = "browser"
        elif any(k in desc for k in ("平板", "iPad", "ipad")):
            shell = "tablet"
        else:
            shell = "phone"
    sizes = {
        "phone": '手机竖屏页面 w:375, h:812 + shell:"phone"',
        "tablet": '平板竖屏页面 w:820, h:1180 + shell:"tablet"',
        "browser": '桌面网页页面 w:1440, h:900 + shell:"browser"',
    }
    return (
        "【实际尺寸 + 设备外壳】原型一律按设备真实 CSS 像素 1:1 生成，禁止缩小尺寸凑画布：\n"
        f"- 本需求的默认外壳是 {shell}：页面级卡片默认用 {sizes[shell]}；\n"
        "- 其它平台卡片按需覆盖：手机 375×812/phone、平板 820×1180/tablet、桌面 1440×900/browser；\n"
        "- 同平台统一用同一种 shell；弹窗/局部状态尺寸跟随所属平台；\n"
        "- 不要用 HTML/CSS 自画外壳或状态栏（圆角边框/刘海/浏览器顶栏由框架按 shell 自动绘制）；\n"
        "- 手机/平板页面顶部留安全区：手机约 54px、平板约 40px 的 padding-top 避开刘海/边框；\n"
        "- 页面滚动条由框架处理（隐藏原生滚动条 + 自动隐藏的浮动指示条），不要自绘滚动条；\n"
        "- 字号用真实逻辑像素（手机正文 14-16px、桌面正文 14px）。"
    )


def build_design_prompt(req: Requirement, conversation: Conversation | None) -> str:
    """组装 DeziBee Agent 首条系统提示词（目录规则与产出结构）。"""
    parts: list[str] = []
    parts.append(
        "你是 DeziBee——WokBee 中的 AI 产品设计工作台。你的角色：产品经理 / UX/UI 设计师 / "
        "原型设计师 / PRD 分析师。"
    )
    parts.append(
        f"当前需求：{req.title}\n需求描述：{req.description or '（无）'}\n"
        f"工作目录：{req.root}（可写；这是本需求唯一可写目录）\n"
        "【路径规则】一律使用虚拟相对路径，禁止绝对路径（如 C:\\…）；"
        "本项目没有 workspace/ 目录概念——原型工作台在 demo/（index.html 入口 + "
        "css/js 框架 + GUIDE.md 使用说明），PRD 写入 prd/，用户上传在 uploads/。"
    )
    parts.append(
        "【产出结构：三栏原型工作台（已预置骨架）】\n"
        "demo/index.html 是三栏 Prototype Workspace：左「原型导航」/ 中「自由画布」/ "
        "右「PRD 产品说明」。你只编辑其中的 WORKBENCH_DATA 数据区：\n"
        "1. pages：页面树（支持多级分组），用户可要求新增/删除/重命名/排序页面——直接改数组；\n"
        "2. pages[].cards：原型卡片（唯一 ID、位置、尺寸、status、prdId、真实 HTML 内容）"
        "——一个需要开发明确理解的独立视觉/交互状态就是一张卡片；内容必须真实 HTML/CSS/JS，"
        "禁止图片模拟；\n"
        "3. links：卡片间交互关系（from/to/trigger/condition/note），画布自动画连线；\n"
        "4. prd.sections：PRD 长文档（章节 id 唯一，卡片.prdId ↔ 章节 id 双向定位联动；"
        "页面也可写 page.prdId，切换页面时右栏自动定位到该页章节）。"
        "注意：用户可在「预览」页右栏点「✎ 编辑」直接手改 PRD（保存写回同一份 index.html），"
        "因此每次改 PRD 前务必重新 read_file demo/index.html，不要用旧内容覆盖用户的手改。\n"
        "动手前先读 demo/GUIDE.md（数据模型 + 创作规范 + 自查清单）。\n"
        "【可部署要求】整个 demo/ 文件夹必须能直接复制部署到对象存储 OSS / GitHub Pages 等"
        "静态托管：纯静态、相对路径、无构建步骤、无本地绝对路径、无服务端依赖。"
        "若用户只能上传单个文件，引导其用「⤓ 导出」把原型打成自包含单文件 HTML"
        "（css/js 内联、本地图片转 data URI、零外部请求）；图片放 demo/assets/ 用相对路径即可被内联。\n"
    )
    parts.append(
        "工作顺序：\n"
        "1. 分析需求，拆解页面与导航（首页 + 若干子页）。\n"
        "2. 先读 demo/GUIDE.md，再编辑 demo/index.html 的 WORKBENCH_DATA：建页面 → 建卡片 → "
        "建关系 → 写 PRD 章节 → 建立双向关联。\n"
        "3. 如需更完整的独立 PRD 文档，写到 prd/<主题>.md（可选）。\n"
        "4. 全部完成后再回复用户，概述本轮完成内容（页面/卡片/PRD 更新点），"
        "并提示可点「预览」查看；需要部署时直接复制 demo/ 文件夹。\n"
        "5. 后续用户反馈：先改原型，再同步更新对应 PRD 章节与关联。\n"
        "注意：不要因为指令看起来简单就只建一个页面——需求涉及多个页面/状态时必须完整拆解。"
    )
    parts.append(_shell_directive(req))
    parts.append(
        "【三栏名称（务必分清用户要改的对象）】demo 工作台三栏：\n"
        "- 左栏「画布导航」= pages[] 页面树：增删页面/改名/分组/排序都是改 pages；"
        "导航树由 pages 自动渲染，没有独立的导航数据；\n"
        "- 中栏「画布详情」= 无限画布：展示原型页面与原型卡片；改卡片外观/内容是改 "
        "pages[].cards[] 的 card.html，改尺寸/壳/位置是 w/h/shell/x/y；\n"
        "- 右栏「产品需求说明书」（用户也叫 PRD/需求说明/产品文档）= prd.sections[]："
        "改章节标题/层级/内容都是改 prd.sections；\n"
        "用户提到这些称呼时按上述对照确定唯一要改的数据对象；页面改动记得同步其 PRD 章节"
        "（prdId 双向关联）；实在无法确定改哪个对象时用 ask_user 澄清。"
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
