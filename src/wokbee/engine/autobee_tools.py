"""AutoBee 定时任务能力：供 WokBee Agent 调用的工具。

Agent 把用户「做成一个 autobee 的自动任务，在每天下午 6 点半执行」这类需求，
解析成结构化字段后调用本工具，即可在 AutoBee 里创建定时任务并注册到调度器。

信息不确定时，工具会用与 `ask_user` 相同的 interrupt 机制暂停，向用户弹窗提问
（桌面端）或发送到手机（网关端）确认后继续创建。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from apscheduler.triggers.cron import CronTrigger
from langchain_core.tools import tool
from langgraph.types import interrupt

from autobee.core.models import ScheduledTask, TaskType
from autobee.core.store import AutoBeeStore
from autobee.engine.nl_builder import NLBuilder
from autobee.engine.scheduler import describe_cron, get_global_scheduler

from wokbee.core.project_store import ProjectStore
from wokbee.engine.ask_user import normalize_ask_user_value

_TASK_TYPE_HINT = "任务类型，text=文本，script=脚本，wokbee=运行 WokBee 项目任务"


def _valid_cron(expr: str | None) -> bool:
    expr = (expr or "").strip()
    if not expr:
        return False
    try:
        CronTrigger.from_crontab(expr)
        return True
    except (ValueError, TypeError):
        return False


def _coerce_cron(text: str) -> str:
    """把常见的中文/简单时间说法转成 5 段 cron；无法识别返回空串。"""
    t = (text or "").strip().lower().replace("：", ":").replace("点", ":").replace("时", ":")
    t = re.sub(r"\s+", "", t)
    if not t:
        return ""

    m = re.search(r"每(\d+)分钟", t)
    if m:
        return f"*/{int(m.group(1))} * * * *"

    ampm = 0
    if re.search(r"凌晨|早上|上午", t):
        ampm = 0
    elif re.search(r"下午|晚上|傍晚", t):
        ampm = 12

    def _hour_min(expr: str):
        mm = re.search(r"(\d{1,2}):(\d{1,2})", expr)
        if mm:
            return int(mm.group(1)), int(mm.group(2))
        # 「X点半」→ 30 分（替换后形如 "6:半"）
        mm = re.search(r"(\d{1,2}):半", expr)
        if mm:
            return int(mm.group(1)), 30
        mm = re.search(r"(\d{1,2}):", expr)
        if mm:
            return int(mm.group(1)), 0
        return None, None

    h, mi = _hour_min(t)
    if h is None:
        return ""

    # 周几
    dow_names = {
        "周一": "1", "星期二": "2", "周二": "2", "星期三": "3", "周三": "3",
        "星期四": "4", "周四": "4", "星期五": "5", "周五": "5",
        "星期六": "6", "周六": "6", "星期日": "0", "周日": "0", "星期天": "0", "周天": "0",
    }
    dow = ""
    for name, val in dow_names.items():
        if name in t:
            dow = val
            break

    h = (h + ampm) % 24
    if "工作日" in t or "每个工作日" in t:
        return f"{mi} {h} * * 1-5"
    if dow:
        return f"{mi} {h} * * {dow}"
    return f"{mi} {h} * * *"


def _answers_map(response: dict) -> dict[str, dict]:
    """把 ask_user 的答案列表规整为 {qid: {selected, custom}}。"""
    out: dict[str, dict] = {}
    for item in (response.get("answers") or []):
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or "")
        selected = item.get("selected") or []
        if isinstance(selected, str):
            selected = [selected]
        out[qid] = {
            "selected": [str(x).strip() for x in selected if str(x).strip()],
            "custom": str(item.get("custom") or "").strip(),
        }
    return out


def _pick(entry: dict | None) -> str:
    """取用户回答的首个选中项或自定义内容。"""
    if not entry:
        return ""
    if entry.get("custom"):
        return entry["custom"]
    sel = entry.get("selected") or []
    return sel[0] if sel else ""


class _TaskDraft:
    """创建中的任务草稿，便于工具内部迭代补全。"""

    def __init__(self) -> None:
        self.name = ""
        self.description = ""
        self.task_type = TaskType.TEXT
        self.schedule = ""
        self.cron_text = ""
        self.content = ""
        self.use_ai = False
        self.code = ""
        self.script_lang = "python"
        self.project_id = ""
        self.webhook_url = ""
        self.push_wecom = False

    def apply_config(self, config: dict) -> None:
        c = config or {}
        if str(c.get("content") or "").strip() and not self.content:
            self.content = str(c["content"])
        if str(c.get("code") or "").strip() and not self.code:
            self.code = str(c["code"])
        lang = str(c.get("script_lang") or "python").lower()
        if lang in ("js", "javascript", "node"):
            self.script_lang = "javascript"
        if str(c.get("project_id") or "").strip() and not self.project_id:
            self.project_id = str(c["project_id"])
        if c.get("push_wecom"):
            self.push_wecom = True
            if str(c.get("webhook_url") or "").strip():
                self.webhook_url = str(c["webhook_url"])

    def summary(self, task_id: str) -> str:
        type_label = self.task_type.label
        cron = describe_cron(self.schedule) or self.cron_text or self.schedule
        lines = [
            f"成功：已创建 AutoBee 定时任务。",
            f"任务ID：{task_id}",
            f"名称：{self.name or '（未命名）'}",
            f"类型：{type_label}",
            f"时间：{cron}（{self.schedule}）",
        ]
        if self.task_type == TaskType.TEXT and not self.use_ai and self.content:
            lines.append(f"内容：{self.content[:60]}")
        if self.task_type == TaskType.SCRIPT and self.code:
            lines.append(f"脚本：{self.script_lang}，{len(self.code)} 字符")
        if self.task_type == TaskType.WOKBEE and self.project_id:
            lines.append(f"项目：{self.project_id}")
        if self.push_wecom:
            lines.append("推送：企业微信已开启")
        lines.append(f"状态：{'已启用' if self.schedule else '未设置时间（未调度）'}")
        return "\n".join(lines)


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

        # ③ 最终校验：仍缺时间则放弃。
        if not _valid_cron(d.schedule):
            return (
                "无法确定定时任务的执行时间，未创建。"
                "请明确说明执行时间（如：每天下午 6 点半），再让我重试。"
            )
        if not d.name:
            d.name = (d.description or "新的定时任务")[:15]

        task = store.create(
            name=d.name,
            description=d.description,
            task_type=d.task_type,
            schedule=d.schedule,
            cron_text=d.cron_text,
            content=d.content,
            use_ai=d.use_ai,
            code=d.code,
            script_lang=d.script_lang,
            project_id=d.project_id,
            push_wecom=d.push_wecom,
            webhook_url=d.webhook_url,
        )
        scheduler = get_global_scheduler()
        if scheduler is not None:
            try:
                scheduler.add_or_update(task)
            except Exception:
                pass
        if emit:
            try:
                emit(
                    "info",
                    f"已创建 AutoBee 定时任务：{task.name}（{describe_cron(task.schedule) or task.schedule}）",
                    {"autobee_task_id": task.id},
                )
            except Exception:
                pass
        return d.summary(task.id)

    create_scheduled_task.name = "create_scheduled_task"
    list_scheduled_tasks.name = "list_scheduled_tasks"
    return [list_scheduled_tasks, create_scheduled_task]


AUTOBEE_TOOL_NAMES = (
    "create_scheduled_task",
    "list_scheduled_tasks",
)
