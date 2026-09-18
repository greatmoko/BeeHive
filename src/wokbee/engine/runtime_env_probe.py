"""运行环境探测：只负责发现本机工具与 shell 版本。"""

from __future__ import annotations

import locale
import os
import platform
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from wokbee.engine.runtime_env_model import RuntimeEnv

# （展示名、PATH 命令名、版本参数；None 表示只记录路径）
_CLI_TOOLS: tuple[tuple[str, str, list[str] | None], ...] = (
    ("git", "git", ["--version"]),
    ("gh", "gh", ["--version"]),
    ("node", "node", ["--version"]),
    ("npm", "npm", ["--version"]),
    ("pnpm", "pnpm", ["--version"]),
    ("yarn", "yarn", ["--version"]),
    ("python", "python", ["--version"]),
    ("pip", "pip", ["--version"]),
    ("uv", "uv", ["--version"]),
    ("conda", "conda", ["--version"]),
    ("docker", "docker", ["--version"]),
    ("go", "go", ["version"]),
    ("rustc", "rustc", ["--version"]),
    ("cargo", "cargo", ["--version"]),
    ("dotnet", "dotnet", ["--version"]),
    ("java", "java", ["-version"]),
    ("mvn", "mvn", ["--version"]),
    ("gradle", "gradle", ["--version"]),
    ("make", "make", ["--version"]),
    ("cmake", "cmake", ["--version"]),
    ("curl", "curl", ["--version"]),
    ("wget", "wget", ["--version"]),
    ("ffmpeg", "ffmpeg", ["-version"]),
    ("bash", "bash", ["--version"]),
    ("sh", "sh", None),
    ("cmd", "cmd", None),
)

def _run_version(argv: list[str], *, timeout: float = 1.2) -> str:
    try:
        r = subprocess.run(  # noqa: S603
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
        )
        out = (r.stdout or r.stderr or "").strip()
        return out.splitlines()[0][:120] if out else ""
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""


def _which(name: str) -> str:
    p = shutil.which(name)
    return str(Path(p).resolve()) if p else ""


def _probe_cli_tools() -> tuple[dict[str, str], dict[str, str]]:
    paths: dict[str, str] = {}
    jobs: list[tuple[str, str, list[str]]] = []
    for label, cmd, ver_args in _CLI_TOOLS:
        exe = _which(cmd)
        if not exe:
            continue
        paths[label] = exe
        if ver_args:
            jobs.append((label, exe, ver_args))

    versions: dict[str, str] = {}
    if not jobs:
        return paths, versions

    def _one(job: tuple[str, str, list[str]]) -> tuple[str, str]:
        label, exe, ver_args = job
        return label, _run_version([exe, *ver_args])

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_one, job) for job in jobs]
        try:
            for fut in as_completed(futures, timeout=6):
                try:
                    label, ver = fut.result()
                    if ver:
                        versions[label] = ver
                except Exception:
                    continue
        except TimeoutError:
            # 整体超时：返回已收集的结果，别让首次环境探测成为硬依赖
            pass
    return paths, versions


def _do_probe_runtime_env() -> RuntimeEnv:
    """实际探测（耗时）；结果应持久化，勿在每次 Agent 运行时调用。"""
    tool_paths, tool_versions = _probe_cli_tools()
    env = RuntimeEnv(
        os_name=platform.system(),
        os_release=platform.release(),
        machine=platform.machine(),
        cwd=str(Path.cwd()),
        python_exe=str(Path(sys.executable).resolve()),
        python_version=platform.python_version(),
        comspec=os.environ.get("COMSPEC", ""),
        encoding=locale.getpreferredencoding(False) or "utf-8",
        tool_paths=tool_paths,
        tool_versions=tool_versions,
        probed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    env.pwsh_exe = _which("pwsh")
    # shutil.which 已按 PATHEXT 补 .exe，“powershell” 即可命中 powershell.exe，无需再补全名。
    env.powershell_exe = _which("powershell")

    shell_jobs: list[tuple[str, list[str]]] = []
    if env.pwsh_exe:
        shell_jobs.append(
            ("pwsh", [env.pwsh_exe, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"])
        )
    if env.powershell_exe:
        shell_jobs.append(
            (
                "ps5",
                [env.powershell_exe, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
            )
        )

    if shell_jobs:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = {pool.submit(_run_version, argv): key for key, argv in shell_jobs}
            try:
                for fut in as_completed(futs, timeout=4):
                    key = futs[fut]
                    try:
                        ver = fut.result()
                    except Exception:
                        ver = ""
                    if key == "pwsh":
                        env.pwsh_version = ver
                    elif key == "ps5":
                        env.powershell_version = ver
            except TimeoutError:
                pass

    if env.pwsh_exe and not env.pwsh_version:
        env.pwsh_version = _run_version([env.pwsh_exe, "-NoProfile", "-v"])

    path_val = os.environ.get("PATH", "")
    if path_val:
        parts = path_val.split(os.pathsep)
        env.path_preview = f"{len(parts)} 目录（前 3：{'; '.join(parts[:3])}）"

    return env

