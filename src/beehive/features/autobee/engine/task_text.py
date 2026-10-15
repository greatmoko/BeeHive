"""文本定时任务执行。"""
from __future__ import annotations
from beehive.system.prompts import AUTOBEE_TEXT_SYSTEM_PROMPT
import logging
from beehive.system.providers.ai_client import AIClient
from beehive.features.autobee.core.models import ScheduledTask

logger = logging.getLogger("autobee")

def run_text(executor, task: ScheduledTask) -> dict:
    if not task.use_ai:
        return {"ok": True, "message": task.content or "（无内容）", "error": ""}
    try:
        model = executor._resolve_model(task, for_gen=True)
        client = AIClient(
            model.api_host, model.api_key, model.model_id,
            family=model.family, protocol=model.api_protocol,
        )
        prompt = task.content or task.description or "请生成一段文本。"
        resp = client.chat(
            [
                {"role": "system", "content": AUTOBEE_TEXT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=1000,
        )
        content = (resp.content or "").strip() or (resp.reasoning_content or "").strip()
        if not content:
            return {"ok": False, "message": "", "error": "AI 未返回正文"}
        return {"ok": True, "message": content, "error": ""}
    except Exception as e:
        return {"ok": False, "message": "", "error": str(e)}
