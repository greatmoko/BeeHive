"""按职责组织的 script_execution。"""
from __future__ import annotations
import json
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from beehive.features.wokbee.execution.archive_guard import command_touches_archives
from beehive.common.process.subprocess_util import run_cancellable
from beehive.system.runtime.runtime_env_cache import collect_runtime_env
from beehive.system.runtime.runtime_env_format import enrich_shell_env
from beehive.features.wokbee.pipeline.models import PhaseResult
from beehive.features.wokbee.pipeline.models import ScriptRunItem
from beehive.features.wokbee.pipeline.validation import resolve_pipeline_script_path


from beehive.features.wokbee.pipeline.script_outputs import _persist_script_callback


from beehive.features.wokbee.pipeline.script_outputs import publish_pipeline_outputs


def _script_touches_archives(cmd, script_file: Path) -> bool:
    """脚本命令或内容是否触及 archives/（与 execute 同一套检查）。

    固化脚本由 subprocess 直跑，绕过 execute 的归档守卫；这里补一层：命令串
    （shell 命令或 argv 拼接）命中即拒，文本脚本内容也可能内嵌 archive 引用。
    command_touches_archives 对「仅提到 archives 字样」偏保守，本系统禁止归档作数据源，
    宁可误拒也放行违规脚本。
    """
    probe = cmd if isinstance(cmd, str) else " ".join(str(x) for x in cmd)
    if command_touches_archives(probe):
        return True
    if script_file.suffix.lower() in {".py", ".sh", ".bat", ".cmd", ".ps1", ".js", ".vbs"}:
        try:
            head = script_file.read_text(encoding="utf-8", errors="replace")[:20000]
        except OSError:
            head = ""
        if command_touches_archives(head):
            return True
    return False


def run_one_script(
    project_root: Path,
    entry: dict,
    *,
    timeout_sec: int = 120,
    cancel_event: threading.Event | None = None,
) -> ScriptRunItem:
    import os

    root = Path(project_root)
    rel = str(entry.get("path") or "")
    desc = str(entry.get("description") or rel)
    step_id = str(entry.get("id") or "")
    root = root.resolve()
    script_file = resolve_pipeline_script_path(root, rel)
    if script_file is None:
        return ScriptRunItem(
            path=rel,
            ok=False,
            error=(
                "脚本路径必须是项目 memory/ 或 uploads/ 目录内的相对路径，"
                "且文件必须真实存在"
            ),
            description=desc,
            step_id=step_id,
        )
    env = enrich_shell_env(os.environ.copy(), project_root=project_root)
    suffix = script_file.suffix.lower()
    if suffix == ".py":
        cmd = [sys.executable, "-X", "utf8", str(script_file)]
        use_shell = False
    elif suffix in {".bat", ".cmd"}:
        cmd = ["cmd", "/c", str(script_file)]
        use_shell = False
    elif suffix == ".ps1":
        cmd = collect_runtime_env(project_root=project_root).powershell_argv_for_file(script_file)
        use_shell = False
    elif suffix == ".js":
        cmd = ["node", str(script_file)]
        use_shell = False
    elif suffix == ".sh":
        cmd = ["bash", str(script_file)]
        use_shell = False
    elif suffix == ".vbs":
        cmd = ["cscript", "//Nologo", str(script_file)]
        use_shell = False
    elif suffix == ".json":
        # JSON 脚本只允许 argv；command 会重新进入 shell，不能作为自动管线步骤。
        try:
            data = json.loads(script_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            return ScriptRunItem(
                path=rel,
                ok=False,
                error=f"JSON 脚本无效：{e}",
                description=desc,
                step_id=step_id,
            )
        if isinstance(data, dict) and isinstance(data.get("argv"), list) and data["argv"]:
            cmd = [str(x) for x in data["argv"]]
            use_shell = False
            allowed = {
                "python", "python3", "python.exe", "py",
                "node", "nodejs", "node.exe", "pwsh", "powershell", "powershell.exe",
                "cmd", "cmd.exe", "bash", "cscript", "cscript.exe",
                Path(sys.executable).name.lower(),
            }
            if Path(cmd[0]).name.lower() not in allowed:
                return ScriptRunItem(
                    path=rel,
                    ok=False,
                    error="JSON argv 仅允许 Python、Node、PowerShell、cmd、bash、cscript 解释器",
                    description=desc,
                    step_id=step_id,
                )
            for arg in cmd[1:]:
                if re.match(r"^[a-zA-Z]:[/\\]|^[/\\]{1,2}", arg):
                    try:
                        Path(arg).resolve().relative_to(root)
                    except (OSError, ValueError):
                        return ScriptRunItem(
                            path=rel,
                            ok=False,
                            error="JSON argv 不得引用项目目录之外的绝对路径",
                            description=desc,
                            step_id=step_id,
                        )
        else:
            return ScriptRunItem(
                path=rel,
                ok=False,
                error="JSON 脚本仅支持非空 argv，不支持 shell command",
                description=desc,
                step_id=step_id,
            )
    else:
        return ScriptRunItem(
            path=rel,
            ok=False,
            error=f"不支持的脚本格式：{suffix or '(无扩展名)'}",
            description=desc,
            step_id=step_id,
        )
    # 归档守卫：脚本直跑绕过 execute 的 command_touches_archives，这里补一层拦截
    if _script_touches_archives(cmd, script_file):
        return ScriptRunItem(
            path=rel,
            ok=False,
            error="脚本命令或内容触及 archives/，已拒绝执行（归档不可作为数据来源）",
            description=desc,
            step_id=step_id,
        )
    try:
        result = run_cancellable(
            cmd,
            cwd=str(root),
            timeout=timeout_sec,
            env=env,
            shell=use_shell,
            cancel_event=cancel_event,
        )
        out = result.stdout
        err = result.stderr
        # 约定：固化脚本失败路径都会输出固定哨兵「脚本执行失败」并退出非零码（见 script_factory.py）。
        # 以「退出码为 0 且全量输出不含该哨兵」为成功判定 —— 既往只查 out[:80] 与任意「失败：」
        # 子串，会在失败信息落到中后段、或脚本 _save("失败…") 后 exit(0) 时误判成功。
        # 哨兵可能落在 stdout 或 stderr，两者都要查。
        ok = result.returncode == 0 and "脚本执行失败" not in out and "脚本执行失败" not in err
        # 主机兜底：无论脚本内部是否 _save，都把输出落到项目根目录
        persist_body = out if out else err
        if persist_body:
            saved = _persist_script_callback(root, rel, persist_body)
            if saved and saved not in (out or ""):
                out = (out or "") + (f"\n[callback 已写入] {saved}" if out else f"[callback 已写入] {saved}")
        if result.cancelled:
            error = "已取消"
        elif result.timed_out:
            error = f"超时（>{timeout_sec}s）"
        else:
            error = (err[:2000] if err else "") or ("" if ok else out[:2000])
        return ScriptRunItem(
            path=rel,
            ok=ok and not result.timed_out and not result.cancelled,
            output=out[:8000],
            error=error,
            description=desc,
            step_id=step_id,
        )
    except OSError as e:
        return ScriptRunItem(
            path=rel,
            ok=False,
            error=str(e),
            description=desc,
            step_id=step_id,
        )


def run_script_phase(
    project_root: Path,
    steps: list[dict],
    *,
    timeout_sec: int = 120,
    cancel_event: threading.Event | None = None,
) -> PhaseResult:
    phase = PhaseResult(type="script")
    outputs: list[str] = []
    for entry in steps:
        item = run_one_script(
            project_root, entry,
            timeout_sec=timeout_sec, cancel_event=cancel_event,
        )
        phase.items.append(item)
        if item.output:
            outputs.append(f"### [{item.step_id or item.path}] {item.description}\n{item.output[:4000]}")
        if not item.ok:
            phase.ok = False
            phase.error = item.error or "脚本失败"
            break  # 有序执行：失败则停在本步，交给 AI 或中止
    phase.output = "\n\n".join(outputs)
    return phase
