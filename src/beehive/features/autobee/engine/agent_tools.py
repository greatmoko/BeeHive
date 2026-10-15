"""Agent 使用的 AutoBee 任务列表、草稿补全和创建工具。"""
from __future__ import annotations
import logging
import re
from typing import Any, Literal
from langchain_core.tools import tool
from langgraph.types import interrupt
from beehive.features.autobee.core.models import TaskType
from beehive.features.autobee.core.store import AutoBeeStore
from beehive.features.autobee.engine.nl_builder import NLBuilder
from beehive.features.autobee.engine.cron_description import describe_cron
from beehive.features.wokbee.projects.project_store import ProjectStore
from beehive.system.agent.ask_user import normalize_ask_user_value
from beehive.features.autobee.engine.task_draft import _valid_cron, _coerce_cron, _answers_map, _pick, _TaskDraft
from beehive.features.autobee.engine.task_registration import register_task

logger = logging.getLogger("wokbee")
_TASK_TYPE_HINT = "任务类型，text=文本，script=脚本，wokbee=运行 WokBee 项目任务"

def build_autobee_tools(
    *,
    resolved=None,
    provider_store=None,
    project_store: ProjectStore | None = None,
    emit=None,
) -> list[Any]:
    """构造 AutoBee 定时任务工具（供 create_deep_agent 挂载）。

    - resolved：当前模型（用于把自然语言描述解析成 cron 配置的兜底）
    - provider_store / project_store：解析厂商模型与校验项目 ID
    """
    store = AutoBeeStore()
    pstore = project_store or ProjectStore()
    nl = NLBuilder(provider_store)
    resolved_model = resolved

    def _ask(questions: list[dict]) -> dict:
        payload = normalize_ask_user_value({"type": "ask_user", "questions": questions})
        if emit:
            try:
                emit("info", f"AI 创建定时任务需向你确认：{len(questions)} 项。", {"ask_user": payload})
            except Exception:
                pass
        return interrupt(payload)

    @tool
    def list_scheduled_tasks() -> str:
        """列出当前已存在的 AutoBee 定时任务。用户问有哪些自动任务、定时任务时使用。"""
        tasks = store.list_tasks()
        if not tasks:
            return "目前还没有任何 AutoBee 定时任务。"
        lines = ["现有 AutoBee 定时任务："]
        for t in tasks:
            cron = describe_cron(t.schedule) or t.cron_text or t.schedule
            state = "启用" if t.enabled else "停用"
            lines.append(
                f"- [{state}] {t.name or '（未命名）'}（{t.task_type.label}）{cron}；ID={t.id}"
            )
        return "\n".join(lines)

    @tool
    def create_scheduled_task(
        name: str = "",
        description: str = "",
        schedule: str = "",
        type: Literal["text", "script", "wokbee"] = "text",
        content: str = "",
        use_ai: bool = False,
        code: str = "",
        script_lang: str = "python",
        project_id: str = "",
        webhook_url: str = "",
    ) -> str:
        """创建一个 AutoBee 定时任务。

        当用户要求「做成一个自动任务 / autobee 任务 / 定时任务，在某个时间执行」时调用。
        请把用户需求解析成以下字段：
        - name：简短任务名称（中文，15 字内）；没有把握时可留空，系统会询问。
        - description：用户的原始需求描述（用于兜底解析时间与名称）。
        - schedule：5 段 cron（分 时 日 月 周），例如每天 18:30 → "30 18 * * *"。
        - type：text=文本（content 或 use_ai 二选一）；script=脚本；wokbee=跑项目任务（填 project_id）。
        - content：text 类型的固定正文；或 use_ai=True 让模型按描述生成正文。
        - code / script_lang：script 类型脚本。
        - project_id：wokbee 类型的 WokBee 项目 ID。
        - webhook_url：企业微信群机器人地址，填了即把执行结果推送到企业微信。

        信息不确定时工具会自动向你（用户）提问，收到答案后再创建。
        """
        d = _TaskDraft()
        d.name = (name or "").replace("\x00", "").strip()
        d.description = (description or "").strip()
        d.task_type = TaskType(type) if type in ("text", "script", "wokbee") else TaskType.TEXT
        d.schedule = (schedule or "").strip()
        d.content = (content or "").strip()
        d.use_ai = bool(use_ai)
        d.code = (code or "").strip()
        d.script_lang = "javascript" if (script_lang or "").lower() in ("js", "javascript", "node") else "python"
        d.project_id = (project_id or "").strip()
        d.webhook_url = (webhook_url or "").strip()
        d.push_wecom = bool(d.webhook_url)

        # ① 时间：Agent 给的不合法/缺失时，先用 NLBuilder 兜底解析描述，仍不行再提问。
        if not _valid_cron(d.schedule) and d.description and resolved_model is not None:
            try:
                parsed = nl.generate(d.description, resolved_model)
                if parsed and _valid_cron(str(parsed.get("schedule") or "")):
                    d.schedule = str(parsed["schedule"])
                    d.cron_text = str(parsed.get("cron_text") or "")
                    if not d.name:
                        d.name = str(parsed.get("name") or "").strip()
                    ptype = str(parsed.get("type") or "").strip().lower()
                    if ptype in ("text", "script", "wokbee"):
                        d.task_type = TaskType(ptype)
                    d.apply_config(parsed.get("config") or {})
            except Exception:
                pass

        # ② 逐项补全不确定信息：可多轮提问，直到必要信息齐全。
        for _round in range(6):
            questions: list[dict] = []

            if not d.name:
                questions.append({
                    "id": "q_name",
                    "prompt": "这个定时任务叫什么名字？（简短，15 字内）",
                    "mode": "single",
                    "options": ["每日提醒", "自动任务", "定时代理"],
                    "allow_custom": True,
                })
            if not _valid_cron(d.schedule):
                questions.append({
                    "id": "q_schedule",
                    "prompt": "请确定定时任务的执行时间（例如：每天 18:30、每个工作日 09:00、每 30 分钟，或直接输入 cron 表达式）",
                    "mode": "single",
                    "options": ["每天 09:00", "每天 18:30", "每个工作日 09:00", "每 30 分钟"],
                    "allow_custom": True,
                })
            if (
                d.task_type == TaskType.TEXT
                and not d.content
                and not d.use_ai
                and not d.push_wecom
            ):
                questions.append({
                    "id": "q_what",
                    "prompt": "这个定时任务到点后要做什么？",
                    "mode": "single",
                    "options": ["生成一段 AI 文本", "执行脚本", "运行 WokBee 项目任务", "推送到企业微信"],
                    "allow_custom": True,
                })
            if d.task_type == TaskType.WOKBEE and not d.project_id:
                projects = pstore.list_projects()
                opts = [f"{p.title}（{p.id}）" for p in projects[:8]] or ["（暂无项目）"]
                questions.append({
                    "id": "q_project",
                    "prompt": "请选择要关联的 WokBee 项目：",
                    "mode": "single",
                    "options": opts,
                    "allow_custom": True,
                })

            if not questions:
                break

            response = _ask(questions)
            if isinstance(response, dict) and response.get("cancelled"):
                return "已取消创建 AutoBee 定时任务（缺少必要信息）。"
            answers = _answers_map(response if isinstance(response, dict) else {})

            if "q_name" in answers:
                val = _pick(answers["q_name"])
                if val:
                    d.name = val[:15]
            if "q_schedule" in answers:
                val = _pick(answers["q_schedule"])
                cron = _coerce_cron(val)
                if not cron and d.description and resolved_model is not None:
                    try:
                        parsed = nl.generate(f"任务时间：{val}。{d.description}", resolved_model)
                        if parsed and _valid_cron(str(parsed.get("schedule") or "")):
                            cron = str(parsed["schedule"])
                            d.cron_text = str(parsed.get("cron_text") or "")
                    except Exception:
                        pass
                if cron:
                    d.schedule = cron
            if "q_what" in answers:
                val = _pick(answers["q_what"])
                if "AI" in val or "生成" in val:
                    d.task_type = TaskType.TEXT
                    d.use_ai = True
                elif "脚本" in val:
                    d.task_type = TaskType.SCRIPT
                elif "WokBee" in val or "项目" in val:
                    d.task_type = TaskType.WOKBEE
                elif "推送" in val or "微信" in val:
                    d.task_type = TaskType.TEXT
                    d.push_wecom = True
                else:
                    d.task_type = TaskType.TEXT
                    d.content = val
                    d.use_ai = False
            if "q_project" in answers:
                val = _pick(answers["q_project"])
                m = re.search(r"（([a-zA-Z0-9_]+)）", val)
                if m:
                    d.project_id = m.group(1)
                else:
                    d.project_id = val

        return register_task(d, store, emit)

    create_scheduled_task.name = "create_scheduled_task"
    list_scheduled_tasks.name = "list_scheduled_tasks"
    return [list_scheduled_tasks, create_scheduled_task]

AUTOBEE_TOOL_NAMES = (
    "create_scheduled_task",
    "list_scheduled_tasks",
)
