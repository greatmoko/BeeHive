"""Agent 定时任务草稿、时间解析与澄清答案归纳。"""
from __future__ import annotations
import re
from apscheduler.triggers.cron import CronTrigger
from beehive.features.autobee.core.models import TaskType
from beehive.features.autobee.engine.cron_description import describe_cron

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
