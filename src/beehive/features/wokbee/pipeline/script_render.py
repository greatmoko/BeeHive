"""按工具类型选择固化模板。"""
from __future__ import annotations
from beehive.features.wokbee.pipeline.script_models import ScriptStep
from beehive.features.wokbee.pipeline.script_commands import _execute_script_label
from beehive.features.wokbee.pipeline.script_commands import _normalize_execute_command
from beehive.features.wokbee.pipeline.script_execute_template import _script_execute
from beehive.features.wokbee.pipeline.script_models import ScriptStep
from beehive.features.wokbee.pipeline.script_commands import _normalize_execute_command, _execute_script_label
from beehive.features.wokbee.pipeline.script_execute_template import _script_execute
from beehive.features.wokbee.pipeline.script_web_templates import _script_web_search, _script_http_get, _script_http_request
def _render_script(step: ScriptStep) -> str | None:
    args = step.args or {}
    if step.tool == "web_search":
        q = str(args.get("query") or args.get("q") or args.get("raw") or "").strip()
        if not q:
            return None
        return _script_web_search(q, int(args.get("max_results") or 5))
    if step.tool == "http_get":
        url = str(args.get("url") or args.get("raw") or "").strip()
        if not url.startswith("http"):
            return None
        return _script_http_get(url, int(args.get("max_chars") or 12000))
    if step.tool == "http_request":
        url = str(args.get("url") or "").strip()
        if not url.startswith("http"):
            return None
        return _script_http_request(
            url,
            method=str(args.get("method") or "GET"),
            headers_json=str(args.get("headers_json") or ""),
            body=str(args.get("body") or ""),
            max_chars=int(args.get("max_chars") or 12000),
        )
    if step.tool == "execute":
        cmd = str(args.get("command") or "").strip()
        label = str(args.get("label") or _execute_script_label(cmd) or "execute")
        if not cmd:
            return None
        # 若尚未规范化，再规范化一次
        norm = _normalize_execute_command(cmd)
        if not norm:
            return None
        cmd, label = norm[0], (args.get("label") or norm[1] or label)
        return _script_execute(cmd, str(label))
    return None
