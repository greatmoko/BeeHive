"""脚本定时任务与解释器命令。"""
from __future__ import annotations
import logging
import sys
from beehive.common.process.subprocess_util import run_cancellable
from beehive.features.autobee.core.models import ScheduledTask

logger = logging.getLogger("autobee")

def run_script(executor, task: ScheduledTask) -> dict:
    code = (task.code or "").strip()
    if not code:
        return {"ok": False, "message": "", "error": "脚本代码为空"}
    timeout = max(1, int(getattr(task, "timeout_s", 120) or 120))
    lang = (getattr(task, "script_lang", None) or "python").strip().lower()
    try:
        cmd = executor._script_cmd(lang, code)
    except FileNotFoundError as e:
        return {"ok": False, "message": "", "error": str(e)}
    try:
        result = run_cancellable(cmd, timeout=timeout)
    except FileNotFoundError as e:
        return {"ok": False, "message": "", "error": str(e)}
    except Exception as e:
        return {"ok": False, "message": "", "error": f"脚本执行失败：{e}"}
    if result.timed_out:
        return {"ok": False, "message": "", "error": f"脚本执行超时（> {timeout} 秒）"}
    if result.cancelled:
        return {"ok": False, "message": "", "error": "脚本执行被取消"}
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if result.returncode == 0:
        return {"ok": True, "message": out or "（执行成功，无输出）", "error": err}
    return {
        "ok": False,
        "message": out or "脚本执行失败",
        "error": err or f"退出码 {result.returncode}",
    }

def script_cmd(lang: str, code: str) -> list[str]:
    """按语言组装解释器命令行。"""
    import shutil

    if lang in ("js", "javascript", "node", "nodejs"):
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            raise FileNotFoundError("未找到 Node.js（node），请先安装并加入 PATH")
        return [node, "-e", code]
    return [sys.executable, "-c", code]
