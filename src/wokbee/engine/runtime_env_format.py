"""运行环境展示与 execute 调用参数格式化。"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from wokbee.engine.runtime_env_cache import (
    _settings_or_default,
    collect_runtime_env,
    ensure_runtime_env,
    get_runtime_env,
)
from wokbee.engine.runtime_env_model import RuntimeEnv

logger = logging.getLogger("wokbee")

def enrich_shell_env(base: dict[str, str] | None = None, *, project_root: str | Path | None = None) -> dict[str, str]:
    """subprocess / execute 用环境：UTF-8 + 注入工具路径，pwsh 目录优先入 PATH。"""
    env = dict(base or os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    rt = ensure_runtime_env()
    env["WOKBEE_PYTHON"] = rt.python_exe
    if rt.pwsh_exe:
        env["WOKBEE_PWSH"] = rt.pwsh_exe
        pwsh_dir = str(Path(rt.pwsh_exe).parent)
        # Windows 环境变量名大小写不敏感，宿主可能是 "Path"；大小写不敏感查找，
        # 避免新建 "PATH" 键挤掉整条原始 PATH（后续子进程找不到 git/python/cmd）。
        path_key = "PATH"
        existing = ""
        for k in list(env.keys()):
            if k.upper() == "PATH":
                path_key = k
                existing = str(env[k] or "")
                break
        if pwsh_dir.lower() not in existing.lower():
            env[path_key] = pwsh_dir + os.pathsep + existing if existing else pwsh_dir
    if project_root:
        env["WOKBEE_PROJECT_ROOT"] = str(Path(project_root).resolve())
    try:
        from wokbee.core.credential_store import inject_vault_env

        inject_vault_env(env)
    except Exception:
        logger.debug("注入凭据环境变量失败", exc_info=True)
    return env


def _format_tool_line(label: str, rt: RuntimeEnv) -> str | None:
    exe = rt.tool_paths.get(label)
    if not exe:
        return None
    ver = rt.tool_versions.get(label, "")
    return f"{label}={exe}" + (f" ({ver})" if ver else "")


def _numbered_environment_text(text: str, prefix: tuple[int, ...] = ()) -> str:
    """Convert the environment block's existing heading/bullet hierarchy to numbers."""
    result, item, subitem = [], 0, 0
    for raw in str(text or "").splitlines():
        stripped = raw.strip()
        if not stripped:
            result.append("")
        elif stripped.startswith("【") and "】" in stripped:
            result.append(f"{'.'.join(map(str, prefix))}. {stripped}" if prefix else f"1. {stripped}")
        elif stripped.startswith("- "):
            item, subitem = item + 1, 0
            number = (*prefix, item) if prefix else (1, item)
            result.append(f"{'.'.join(map(str, number))}. {stripped[2:]}")
        elif stripped.startswith("· "):
            subitem += 1
            number = (*prefix, item, subitem) if prefix else (1, item, subitem)
            result.append(f"{'.'.join(map(str, number))}. {stripped[2:]}")
        else:
            result.append(raw)
    return "\n".join(result)


def format_runtime_env_block(
    rt: RuntimeEnv,
    *,
    project_root: str = "",
    model: str = "",
    policy: str = "",
    extra: str = "",
    design_mode: bool = False,
    for_agent: bool = False,
) -> str:
    """格式化运行环境；Agent 上下文不暴露软件启动目录。"""
    lines = [
        "【运行环境】（WokBee 本机实测；execute 与 scripts 均在此环境执行）",
    ]
    if rt.probed_at:
        lines.append(f"- 环境快照时间：{rt.probed_at}（持久化缓存，非每次 Agent 重探）")
    if rt.os_name.lower() == "windows":
        lines.append(
            "- **平台：Windows（非 Linux/macOS）** — execute 勿用 head/tail/awk/sed/bash 语法；"
            "列目录用 ls 工具，或 pwsh 的 Get-ChildItem / Select-Object"
        )
    agent_root = rt.project_root or project_root or "（未知）"
    lines.extend([
        f"- OS：{rt.os_name} {rt.os_release} ({rt.machine})",
        f"- Agent 工作目录（文件工具与项目文件的基准）：{agent_root}",
        f"- execute 工作目录（脚本和相对路径命令的基准）：{agent_root}",
    ])
    if for_agent:
        lines.extend([
            "- 目录规则：软件运行目录与 Agent 工作目录无关；禁止用软件运行目录搜索项目文件。",
            "- Get-Location 只能验证 execute 当前目录，不能据此推断 uploads/、scripts/ 或 deliverables/ 的位置。",
        ])
    elif rt.cwd:
        lines.append(f"- 应用运行目录（仅系统内部，不是 Agent 工作目录）：{rt.cwd}")
    if design_mode:
        lines.extend([
            "- 文件工具虚拟路径（read_file/write_file/ls/grep/glob 必用，绝不用真实路径；"
            "项目外路径须先 request_access 申请、获批后走 /ext/…）：",
            "  · demo/…  原型产出（index.html 入口，可部署）",
            "  · prd/…  产品说明文档",
            "  · uploads/…  用户上传（含 references/ 参考材料）",
            "  · /skills/…  全局 Skills（只读）",
            "  · /ext/<slug>/…  已授权附加目录（项目外）",
            "  · 示例：demo/index.html（勿写完整 Windows 路径）",
        ])
    else:
        lines.extend([
            "- 文件工具虚拟路径（read_file/write_file/ls/grep/glob 必用，绝不用真实路径；"
            "项目外路径须先 request_access 申请、获批后走 /ext/…）：",
            "  · workspace/…  workspace 沙箱",
            "  · deliverables/…  交付物",
            "  · uploads/…  用户上传",
            "  · memory/…  经验（experiences/）",
            "  · scripts/…  管线脚本",
            "  · uploads/references/…  参考材料（含 Skills 快照）",
            "  · /skills/…  全局 Skills（只读）",
            "  · /ext/<slug>/…  已授权附加目录（项目外；先 request_access 申请→人工高危审批→用返回的 /ext/<slug>/…）",
            "  · 示例：workspace/wttr_shenzhen.json（勿写完整 Windows 路径）",
        ])
    lines.extend([
        f"- Python（WokBee 解释器）：{rt.python_exe} ({rt.python_version})",
    ])
    if rt.comspec:
        lines.append(f"- COMSPEC（cmd）：{rt.comspec}")

    if rt.pwsh_exe:
        ver = f" ({rt.pwsh_version})" if rt.pwsh_version else ""
        lines.append(f"- PowerShell 7+（pwsh，**优先**）：{rt.pwsh_exe}{ver}")
    else:
        lines.append("- PowerShell 7+（pwsh）：未在 PATH 中找到")

    if rt.powershell_exe:
        ver = f" ({rt.powershell_version})" if rt.powershell_version else ""
        lines.append(
            f"- Windows PowerShell 5.x（**备选**，无 pwsh 时用）：{rt.powershell_exe}{ver}"
        )

    lines.extend([
        "- 工具选用约定：",
        "  · 有更新/更好用的本机版本时优先用新版，不要无故退回旧版",
        "  · **列目录/读文件优先 ls、read_file、grep 工具**，少用 execute dir/type/cat",
        "  · execute：WokBee 在 Windows 上**自动经 pwsh 执行**（非 cmd），但仍非 Linux/bash",
        "  · **pwsh 调用带空格路径的程序**：用 `&` 调用符 + 加引号完整路径，如 `& \"C:\\Program Files\\Python313\\python.exe\" script.py 参数`；勿把带引号路径裸放句首当作可执行命令",
        "  · 勿在 execute 中用 head/tail/awk/sed（cmd 无；pwsh 也不保证有）；截断用 Select-Object -First N",
        f"  · Python 命令/脚本优先：& \"{rt.python_exe}\" 或 python（与 WokBee 相同）",
        "  · 尽量用下方列出的 CLI 绝对路径，避免假设默认 PATH",
        "  · .ps1 管线脚本：WokBee 自动按 pwsh → powershell 顺序执行",
        f"- 文本编码：UTF-8（locale={rt.encoding}，execute 输出已尽量转 UTF-8）",
    ])

    groups: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("版本控制", ("git", "gh")),
        ("Node / 前端", ("node", "npm", "pnpm", "yarn")),
        ("Python 生态", ("python", "pip", "uv", "conda")),
        ("容器 / 构建", ("docker", "make", "cmake", "mvn", "gradle")),
        ("语言运行时", ("go", "dotnet", "java", "rustc", "cargo")),
        ("Shell / 网络", ("bash", "sh", "curl", "wget", "ffmpeg")),
    )
    for group_name, labels in groups:
        items = [line for label in labels if (line := _format_tool_line(label, rt))]
        if items:
            lines.append(f"- {group_name}：" + "；".join(items))

    if rt.path_preview:
        lines.append(f"- PATH：{rt.path_preview}")

    if model:
        lines.append(f"- 模型：{model}")
    if policy:
        lines.append(f"- 审核策略：{policy}")

    lines.append("- 禁止：访问 archives/ 归档目录")

    if extra.strip():
        lines.append(extra.strip())

    return "\n".join(lines)


def build_runtime_env_block(
    *,
    project_root: str = "",
    model: str = "",
    policy: str = "",
    extra: str = "",
    settings=None,
    design_mode: bool = False,
) -> str:
    """供【会话上下文】注入的环境说明块（读缓存，无缓存时才探测）。"""
    rt = collect_runtime_env(project_root=project_root or None, settings=settings)
    return format_runtime_env_block(
        rt,
        project_root=project_root,
        model=model,
        policy=policy,
        extra=extra,
        design_mode=design_mode,
        for_agent=True,
    )


def build_runtime_env_settings_text(settings=None) -> str:
    """设置页展示用文本。"""
    settings = _settings_or_default(settings)
    rt = get_runtime_env(settings)
    if rt is None:
        return (
            "尚未探测本机环境。\n\n"
            "首次运行 Agent 时将自动探测并保存；也可点击下方「重新探测」。"
            "之后所有 Agent 均加载此缓存，不会重复扫描。"
        )
    runtime = _numbered_environment_text(format_runtime_env_block(rt), prefix=(1, 2))
    return f"1. 系统环境信息\n1.1. 最后探测：{rt.probed_at or '未知'}\n\n{runtime}"


def build_execute_invocation(command: str, settings=None) -> tuple[list[str] | None, str]:
    """构造 execute 调用参数。Windows 有 pwsh 时用 pwsh -Command，避免默认 cmd 缺 head 等。"""
    rt = ensure_runtime_env(settings)
    if sys.platform == "win32" and rt.pwsh_exe:
        ps_cmd = (
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
            "$OutputEncoding = [Console]::OutputEncoding; "
            + command
        )
        return (
            [
                rt.pwsh_exe,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                ps_cmd,
            ],
            "pwsh",
        )
    if sys.platform == "win32" and rt.powershell_exe:
        ps_cmd = (
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
            "$OutputEncoding = [Console]::OutputEncoding; "
            + command
        )
        return (
            [
                rt.powershell_exe,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                ps_cmd,
            ],
            "powershell",
        )
    return (None, "shell")
