"""自然语言 → 定时任务配置。"""

from __future__ import annotations

from sysprompt import AUTOBEE_CONFIG_SYSTEM_PROMPT

import json
import re

from apscheduler.triggers.cron import CronTrigger

from tokbee.core.ai_client import AIClient
from tokbee.core.provider_store import ResolvedModel


class NLBuilder:
    """调用 AI 模型，把一句话变成定时代码配置。"""

    def __init__(self, provider_store=None):
        self._provider_store = provider_store

    def generate(self, text: str, model: ResolvedModel) -> dict | None:
        """返回解析后的 dict；失败返回 None，同时把原始文本放入 self.last_raw。"""
        self.last_raw = ""
        if not (text or "").strip():
            raise ValueError("请输入要生成的自然语言描述")
        if model is None:
            raise ValueError("请先选择用于生成的 AI 模型")

        client = AIClient(
            model.api_host, model.api_key, model.model_id,
            family=model.family, protocol=model.api_protocol,
        )
        resp = client.chat(
            [
                {"role": "system", "content": AUTOBEE_CONFIG_SYSTEM_PROMPT},
                {"role": "user", "content": f"用户需求：{text.strip()}"},
            ],
            temperature=0.2,
            max_tokens=1200,
        )
        raw = (resp.content or "").strip() or (resp.reasoning_content or "").strip()
        self.last_raw = raw
        parsed = self._parse(raw)
        if parsed is None:
            raise ValueError("AI 未能生成可解析的配置，请重试或手动编辑。")
        return parsed

    @staticmethod
    def _parse(raw: str) -> dict | None:
        """去围栏 → json.loads → 正则兜底；校验 cron 与 type。"""
        text = (raw or "").strip()
        if not text:
            return None
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fence:
            text = fence.group(1).strip()
        data = None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{[\s\S]*\}", text)
            if m:
                try:
                    data = json.loads(m.group(0))
                except json.JSONDecodeError:
                    data = None
        if not isinstance(data, dict):
            return None

        name = str(data.get("name") or "").replace("\x00", "").strip()
        ttype = str(data.get("type") or "text").strip().lower()
        valid_types = {"text", "script", "wokbee"}
        if ttype not in valid_types:
            ttype = "text"  # 含旧语义 wecom → 落入 text，由推送渠道承担
        schedule = str(data.get("schedule") or "").strip()
        if schedule:
            try:
                CronTrigger.from_crontab(schedule)
            except ValueError:
                schedule = ""
        if not schedule:
            return None
        config = data.get("config") if isinstance(data.get("config"), dict) else {}
        config = {str(k): v for k, v in config.items()}
        # 旧模型可能仍输出 wecom：映射为文本 + 开启推送渠道
        if str(data.get("type") or "").strip().lower() == "wecom":
            ttype = "text"
            config.setdefault("push_wecom", True)
        return {
            "name": name or "新的定时任务",
            "type": ttype,
            "schedule": schedule,
            "cron_text": str(data.get("cron_text") or "").replace("\x00", "").strip(),
            "config": config,
        }
