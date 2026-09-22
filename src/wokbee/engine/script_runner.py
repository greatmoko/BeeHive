"""本地执行经验固化脚本，并支持按 pipeline steps 有序推进（script/ai 任意组合）。"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from wokbee.core.paths import scripts_dir
from wokbee.engine.archive_guard import command_touches_archives
from tokbee.core.subprocess_util import run_cancellable
from wokbee.engine.runtime_env import collect_runtime_env, enrich_shell_env

logger = logging.getLogger("wokbee")

EventFn = Callable[[str, str, dict], None]


@dataclass
class ScriptRunItem:
    path: str
    ok: bool
    output: str = ""
    error: str = ""
    description: str = ""
    step_id: str = ""


@dataclass
class PhaseResult:
    """单个有序步骤的执行结果。"""

    type: str  # script | ai
    ok: bool = True
    items: list[ScriptRunItem] = field(default_factory=list)
    ai_steps: list[dict] = field(default_factory=list)
    output: str = ""
    error: str = ""
    index: int = 0


@dataclass
class PipelineRunResult:
    """整条有序管线的状态（可能只跑到第一个脚本失败处）。"""

    ran: bool = False
    ok: bool = False
    items: list[ScriptRunItem] = field(default_factory=list)
    ai_steps: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    phases: list[dict] = field(default_factory=list)
    phase_results: list[PhaseResult] = field(default_factory=list)
    combined_output: str = ""
    error_summary: str = ""
    pipeline_path: Path | None = None
    need_ai: bool = True
    reason: str = ""
    # 交错执行：当前停在第几个 phase（0-based），后续由 runner 继续。
    # 每个 pipeline step 都是一个 phase，保留原始步骤边界，便于状态追踪。
    next_phase_index: int = 0
    # 仅保留脚本阶段产出；AI 阶段结果已在 checkpoint 消息历史中。
    context_parts: list[str] = field(default_factory=list)


def load_pipeline(project_root: Path) -> dict | None:
    path = scripts_dir(project_root) / "pipeline.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("读取 pipeline.json 失败: %s", e)
        return None


def resolve_pipeline_script_path(project_root: Path, rel_path: str) -> Path | None:
    """按 Pipeline 合同解析脚本：仅接受项目内 scripts/ 或 uploads/ 相对路径。"""
    root = Path(project_root).resolve()
    raw = str(rel_path or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:/", raw):
        return None
    parts = raw.split("/")
    if ".." in parts or parts[0].lower() not in {"scripts", "uploads"}:
        return None
    candidate = (root / "/".join(parts)).resolve()
    allowed = (root / parts[0]).resolve()
    try:
        candidate.relative_to(allowed)
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def validate_pipeline_script_paths(
    project_root: Path,
    steps: list[dict] | None = None,
) -> list[str]:
    """返回 pipeline 中缺失或越界的脚本路径，不执行任何脚本。"""
    root = Path(project_root).resolve()
    if steps is None:
        data = load_pipeline(root)
        steps = data.get("steps") if isinstance(data, dict) else []
    missing: list[str] = []
    for step in steps or []:
        if not isinstance(step, dict) or str(step.get("type") or "").lower() != "script":
            continue
        rel = str(step.get("path") or "").replace("\\", "/").strip()
        valid = resolve_pipeline_script_path(root, rel) is not None
        if not valid:
            missing.append(rel or "（空路径步骤）")
    return missing


def normalize_steps(data: dict, *, keep_ai: bool = True) -> list[dict]:
    """统一为可运行的有序 steps（script 与 ai 均可）。

    兼容旧版 scripts + ai_steps。新策略：pipeline 固化首次成功后的完整执行路径——
    `type:script` 确定性机械步骤直接执行（不耗 Token）；`type:ai` 是**已经确定的业务任务**
    （理解/分析/整理/创意/写作等），后续运行照样调用 LLM 执行该固定任务，但禁止重新规划。
    keep_ai=False 时过滤 ai 步骤（仅用于展示/统计）。
    """
    raw = data.get("steps")
    if isinstance(raw, list) and raw:
        out: list[dict] = []
        for i, s in enumerate(raw):
            if not isinstance(s, dict):
                continue
            t = str(s.get("type") or "").lower().strip()
            if t not in ("script", "ai"):
                continue
            if t == "ai" and not keep_ai:
                continue
            step = dict(s)
            step["type"] = t
            step.setdefault("id", f"{t}_{i+1}")
            out.append(step)
        if out:
            return out

    # 兼容：先全部脚本，再全部 AI（同样过滤 ai）
    steps: list[dict] = []
    for i, entry in enumerate(data.get("scripts") or []):
        if not isinstance(entry, dict):
            continue
        steps.append(
            {
                "id": f"script_{i+1}",
                "type": "script",
                "path": entry.get("path") or "",
                "tool": entry.get("tool") or "",
                "description": entry.get("description") or entry.get("path") or "",
                "args": entry.get("args") or {},
            }
        )
    if not keep_ai:
        return steps
    for i, entry in enumerate(data.get("ai_steps") or []):
        if isinstance(entry, dict):
            steps.append(
                {
                    "id": f"ai_{i+1}",
                    "type": "ai",
                    "description": entry.get("description") or "AI 步骤",
                    "prompt_hint": entry.get("prompt_hint") or "",
                }
            )
        else:
            steps.append(
                {
                    "id": f"ai_{i+1}",
                    "type": "ai",
                    "description": str(entry),
                    "prompt_hint": "",
                }
            )
    return steps


def group_phases(steps: list[dict]) -> list[dict]:
    """按 pipeline 数组保留逐步边界，不把连续 script/ai 合并。"""
    return [
        {"type": step.get("type"), "steps": [step]}
        for step in steps
        if isinstance(step, dict)
    ]


def _persist_script_callback(project_root: Path, script_rel: str, body: str) -> str | None:
    """把脚本 stdout/stderr 落到 workspace/script_callback_*.md（主机兜底）。"""
    from datetime import datetime

    text = (body or "").strip()
    if not text:
        return None
    root = Path(project_root)
    ws = root / "workspace"
    try:
        ws.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    stem = Path(script_rel or "script").stem or "script"
    # 自动固化名较长时，尽量用可读尾段
    if "_ai_" in stem:
        stem = stem.split("_ai_", 1)[-1] or stem
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = (
        f"# 脚本 callback：{stem}\n\n"
        f"- 生成时间：{stamp}\n"
        f"- 脚本：{script_rel}\n\n"
        f"---\n\n"
        f"{text}\n"
    )
    out = ws / f"script_callback_{stem}.md"
    try:
        out.write_text(content, encoding="utf-8")
    except OSError:
        return None
    try:
        return out.relative_to(root).as_posix()
    except ValueError:
        return str(out)


def publish_pipeline_outputs(project_root: Path, *, recent_minutes: int = 15) -> list[str]:
    """Copy actual pipeline outputs to deliverables without converting them to Markdown."""
    root = Path(project_root)
    ws = root / "workspace"
    target = root / "deliverables"
    target.mkdir(parents=True, exist_ok=True)
    published: list[str] = []
    infra = {
        ".git", ".vscode", "__pycache__", "scripts", "memory", "uploads",
        "deliverables", "workspace", "archives",
    }

    def copy_to(src: Path, rel: Path) -> None:
        try:
            dst = target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            published.append(rel.as_posix())
        except OSError:
            pass

    if ws.exists():
        for path in sorted(ws.rglob("*")):
            if path.is_file() and not (
                path.name.startswith("script_callback_") and path.suffix.lower() == ".md"
            ):
                copy_to(path, path.relative_to(ws))

    cutoff = time.time() - max(1, int(recent_minutes)) * 60
    top_level = root.iterdir() if root.exists() else []
    for path in top_level:
        if not path.is_file() or path.name in infra:
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                copy_to(path, Path(path.name))
        except OSError:
            continue
    return published


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
                "脚本路径必须是项目 scripts/ 或 uploads/ 目录内的相对路径，"
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
        # 主机兜底：无论脚本内部是否 _save，都把输出落到 workspace
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


def peek_pipeline(project_root: Path) -> PipelineRunResult:
    """读取并规范化管线，不执行。

    steps 可同时含 `script` 与 `ai`：script 直接执行，ai 是已确定的业务任务（仍调 LLM，
    但不重新规划）。不生成任何 final_ai。
    """
    root = Path(project_root)
    pipe_path = scripts_dir(root) / "pipeline.json"
    data = load_pipeline(root)
    result = PipelineRunResult(pipeline_path=pipe_path if pipe_path.exists() else None)
    if not data:
        result.reason = "无 pipeline.json，走完整 AI 流程"
        result.need_ai = True
        return result

    steps = normalize_steps(data)
    result.steps = steps
    result.phases = group_phases(steps)
    result.ran = True
    if not steps:
        result.reason = "pipeline 无步骤"
        result.need_ai = True
        result.ok = True
        return result
    result.reason = f"有序管线共 {len(steps)} 步（逐步执行，不合并相邻步骤）"
    result.need_ai = any(s.get("type") == "ai" for s in steps)
    result.ok = True
    return result


def run_pipeline_until_ai_or_end(
    project_root: Path,
    *,
    start_phase: int = 0,
    timeout_sec: int = 120,
    prior_context: list[str] | None = None,
    cancel_event: threading.Event | None = None,
    step_budget=None,
) -> PipelineRunResult:
    """从 start_phase 起执行脚本；遇到 AI 步骤则暂停并 need_ai。

    若整段均为脚本且成功，则 need_ai=False。
    """
    result = peek_pipeline(project_root)
    if not result.ran or not result.phases:
        return result

    missing = validate_pipeline_script_paths(project_root, result.steps)
    if missing:
        first_missing = next(
            (
                i
                for i, step in enumerate(result.steps)
                if isinstance(step, dict)
                and str(step.get("type") or "").lower() == "script"
                and str(step.get("path") or "").replace("\\", "/").strip()
                in missing
            ),
            0,
        )
        bad = result.steps[first_missing] if result.steps else {}
        item = ScriptRunItem(
            path=str(bad.get("path") or missing[0]),
            ok=False,
            error="pipeline 引用了不存在或越界的脚本：" + "、".join(missing),
            description=str(bad.get("description") or "脚本路径检查"),
            step_id=str(bad.get("id") or ""),
        )
        phase = PhaseResult(
            type="script",
            ok=False,
            items=[item],
            error=item.error,
            index=first_missing,
        )
        result.phase_results.append(phase)
        result.items = [item]
        result.next_phase_index = first_missing
        result.error_summary = item.error
        result.need_ai = True
        result.reason = (
            f"管线第 {first_missing + 1} 步脚本路径检查失败，已暂停；"
            "请先修复 pipeline 脚本引用，再进行异常接管"
        )
        return result

    context = list(prior_context or [])
    result.context_parts = context
    all_items: list[ScriptRunItem] = []

    i = start_phase
    while i < len(result.phases):
        phase = result.phases[i]
        if phase["type"] == "script":
            if step_budget is not None:
                step_budget.consume("pipeline_script")
            pr = run_script_phase(
                project_root, phase["steps"],
                timeout_sec=timeout_sec, cancel_event=cancel_event,
            )
            pr.index = i
            result.phase_results.append(pr)
            all_items.extend(pr.items)
            if pr.output:
                context.append(
                    f"## 阶段 {i+1}（脚本）\n{pr.output}"
                )
            if not pr.ok:
                result.items = all_items
                result.ok = False
                result.combined_output = "\n\n".join(context)
                result.context_parts = context
                result.error_summary = "\n".join(
                    f"- `{it.path}`: {it.error or '失败'}" for it in pr.items if not it.ok
                )
                result.need_ai = True
                result.next_phase_index = i
                result.ai_steps = []  # 本阶段失败，交给 AI 补救本步
                result.reason = (
                    f"管线第 {i+1} 步（脚本）失败，暂停；"
                    "请 AI 补救后再继续后续步骤"
                )
                return result
            i += 1
            continue

        # AI 步骤：暂停，把当前业务任务交给模型
        result.items = all_items
        result.ok = True
        result.combined_output = "\n\n".join(context)
        result.context_parts = context
        result.next_phase_index = i
        result.ai_steps = list(phase["steps"])
        result.need_ai = True
        # 后续还有什么（告知 AI 不要越权做后面的脚本）
        remaining = result.phases[i + 1 :]
        tail = ""
        if remaining:
            tail = "；本阶段完成后主机将继续执行后续脚本/AI 阶段，请勿越权执行后续脚本步骤"
        result.reason = f"按管线进入第 {i+1} 步（AI）{tail}"
        return result

    # 全部阶段完成（script/ai 均已完成）
    result.items = all_items
    result.ok = True
    result.combined_output = "\n\n".join(context)
    result.context_parts = context
    result.next_phase_index = len(result.phases)
    result.need_ai = False
    result.ai_steps = []
    result.reason = "有序管线全部阶段执行完毕，结束"
    return result


def build_user_message_for_ai_phase(
    *,
    original_message: str,
    pipeline: PipelineRunResult,
    phase_index: int | None = None,
) -> str:
    """构造 AI 步骤/异常接管时的用户消息（含先前脚本上下文与本阶段任务）。

    - ai 步骤：pipeline 固化好的**明确业务任务**（description + prompt_hint），
      后续运行直接执行该固定任务，不重新规划整个 Pipeline；
    - 脚本/步骤失败：AI 接管处理当前异常并完成剩余目标。
    """
    idx = phase_index if phase_index is not None else pipeline.next_phase_index
    if pipeline.ok and pipeline.ai_steps:
        # 正常的 ai 步骤（已确定的业务任务，非自由规划）
        parts = [
            original_message.strip() or "请根据项目目标推进工作。",
            "",
            "【有序执行管线 — AI 步骤】",
            f"说明：{pipeline.reason}",
            "请**只执行下面列出的 AI 业务任务**：不要重新规划整个 Pipeline，"
            "不要自行添加目标里没有的任务；如需验证当前输入，可以重复执行之前的脚本，"
            "但不得越权执行后续步骤。",
        ]
        if pipeline.context_parts or pipeline.combined_output:
            parts.extend(
                [
                    "",
                    "## 此前脚本阶段已产出的上下文（callback 等）",
                    (pipeline.combined_output or "\n\n".join(pipeline.context_parts))[:8000],
                ]
            )
        parts.extend(["", f"## 本阶段 AI 任务（步骤 {idx+1}）"])
        for n, step in enumerate(pipeline.ai_steps, 1):
            if isinstance(step, dict):
                parts.append(f"{n}. {step.get('description') or step}")
                hint = step.get("prompt_hint") or ""
                if hint:
                    parts.append(f"   （{hint}）")
            else:
                parts.append(f"{n}. {step}")
        parts.append(
            "请先读取 workspace/script_callback_*.md 中的脚本 callback（若有）；"
            "完成后把中间结果写入 workspace/（或最终交付写入 deliverables/）；"
            "用户上传文件在 uploads/；同名或相近文件以最新修改时间为准。"
        )
    else:
        # 异常接管：脚本/步骤失败
        parts = [
            original_message.strip() or "请根据项目目标推进工作。",
            "",
            "【有序执行管线 — 异常接管】",
            f"说明：{pipeline.reason}",
            "请只处理当前异常并修复当前步骤；不要执行后续步骤。"
            "允许重复执行之前的脚本来验证修复结果。"
            "如需调整管线，只提交当前失败步骤的最小修正版，"
            "并通过 update_project_experience 固化，不要包办后续阶段。",
        ]
        if pipeline.context_parts or pipeline.combined_output:
            parts.extend(
                [
                    "",
                    "## 此前阶段已产出的上下文（脚本结果等）",
                    (pipeline.combined_output or "\n\n".join(pipeline.context_parts))[:8000],
                ]
            )
        if pipeline.error_summary:
            parts.extend(
                [
                    "",
                    "## 脚本失败（请先补救）",
                    pipeline.error_summary,
                    "补救后把关键结果写入 workspace/script_callback_*.md，主机将按经验顺序继续后续步骤。",
                ]
            )

    # 预告后续阶段，避免 AI 包办
    remaining = []
    for ph in pipeline.phases[idx + 1 :]:
        labels = []
        for s in ph.get("steps") or []:
            labels.append(str(s.get("description") or s.get("path") or s.get("type")))
        remaining.append(f"- [{ph.get('type')}] " + "；".join(labels))
    if remaining:
        parts.extend(
            [
                "",
                "## 后续阶段（由主机按顺序执行，你现在不要做）",
                *remaining,
            ]
        )
    return "\n".join(parts)


def format_order_markdown(steps: list[dict]) -> str:
    """生成 pipeline.json 的 order_markdown 字段（执行顺序说明）。"""
    if not steps:
        return "（暂无有序步骤；下次运行将走完整 AI。）"
    lines = [
        "下次运行将**严格按下列顺序一路执行**：",
        "（script 步骤自动执行、不耗 Token；ai 步骤执行已确定的 AI 业务任务）",
        "",
    ]
    for i, s in enumerate(steps, 1):
        t = s.get("type")
        if t == "script":
            lines.append(
                f"{i}. **[脚本]** `{s.get('path')}` — {s.get('description') or ''}"
            )
        else:
            lines.append(f"{i}. **[AI]** {s.get('description') or 'AI 步骤'}")
            if s.get("prompt_hint"):
                lines.append(f"   - 提示：{s.get('prompt_hint')}")
    lines.append("")
    lines.append("`scripts/` **不参与归档**；本顺序保存在 `scripts/pipeline.json` 的 `steps` 中。")
    return "\n".join(lines)
