"""受注入策略保护的 UTF-8、可取消进程树执行与输出限制。"""
from __future__ import annotations
import os
from pathlib import Path
from deepagents.backends.protocol import ExecuteResponse
from beehive.system.runtime.runtime_env_format import enrich_shell_env
from beehive.system.runtime.runtime_env_format import build_execute_invocation
from beehive.common.process.subprocess_util import run_cancellable

EXECUTE_OUTPUT_MAX_BYTES = 30_000
EXECUTE_OUTPUT_HEAD_CHARS = 10_000
EXECUTE_OUTPUT_TAIL_CHARS = 10_000


def _shell_env(base: dict | None = None, *, project_root: str | Path | None = None) -> dict[str, str]:
    return enrich_shell_env(base, project_root=project_root)

def execute_shell(backend, command: str, *, timeout: int | None = None) -> ExecuteResponse:
    """执行 shell；强制 UTF-8 解码，避免 Windows 默认 GBK 读 UTF-8 输出崩溃。

    这是对 `LocalShellBackend.execute` 的有意分支：执行前调用注入的命令守卫，
    输出统一 UTF-8 解码并截断。上游
    deepagents 升级会改动它的 execute（run/echo/os.environ 等）时，需同步本方法；
    否则归档守卫会随漂移而失效。改动前先diff上游 `backends/local_shell.py`。

    不用 `subprocess.run(timeout=)`：Windows 上孙进程继承管道时，超时杀父进程后
    communicate 仍会永久阻塞。改为墙钟轮询 + 杀进程树；`cancel_event` 供暂停按钮
    在命令执行中途打断（交互/运行模式共用）。
    """
    if backend._command_guard is not None and backend._command_guard(command):
        return ExecuteResponse(output=backend._denied_message, exit_code=1, truncated=False)
    if not command or not isinstance(command, str):
        return ExecuteResponse(
            output="Error: Command must be a non-empty string.",
            exit_code=1,
            truncated=False,
        )

    effective_timeout = timeout if timeout is not None else getattr(
        backend, "_default_timeout", 120
    )
    if effective_timeout <= 0:
        raise ValueError(f"timeout must be positive, got {effective_timeout}")

    max_bytes = int(
        getattr(backend, "_max_output_bytes", EXECUTE_OUTPUT_MAX_BYTES)
        or EXECUTE_OUTPUT_MAX_BYTES
    )
    env = _shell_env(
        getattr(backend, "_env", None),
        project_root=getattr(backend, "root_dir", None),
    )
    cwd = str(getattr(backend, "cwd", os.getcwd()))
    cancel_event = getattr(backend, "cancel_event", None)

    try:
        argv, _shell_mode = build_execute_invocation(command)
        if argv:
            result = run_cancellable(
                argv,
                timeout=float(effective_timeout),
                cancel_event=cancel_event,
                cwd=cwd,
                env=env,
                shell=False,
                max_output_bytes=max_bytes,
            )
        else:
            result = run_cancellable(
                command,
                timeout=float(effective_timeout),
                cancel_event=cancel_event,
                cwd=cwd,
                env=env,
                shell=True,
                max_output_bytes=max_bytes,
            )
        if result.cancelled:
            return ExecuteResponse(
                output="命令已取消：用户点击了暂停，进程树已被终止。",
                exit_code=130,
                truncated=False,
            )
        if result.timed_out:
            if timeout is not None:
                msg = (
                    f"Error: Command timed out after {effective_timeout} seconds "
                    "(custom timeout). The command may be stuck or require more time."
                )
            else:
                msg = (
                    f"Error: Command timed out after {effective_timeout} seconds. "
                    "For long-running commands, re-run using the timeout parameter."
                )
            return ExecuteResponse(output=msg, exit_code=124, truncated=False)
        output_parts: list[str] = []
        if result.stdout:
            output_parts.append(result.stdout)
        if result.stderr:
            stderr_lines = result.stderr.strip().split("\n")
            output_parts.extend(f"[stderr] {line}" for line in stderr_lines)
        output = "\n".join(output_parts) if output_parts else "<no output>"
        truncated = False
        if len(output) > max_bytes:
            head = min(EXECUTE_OUTPUT_HEAD_CHARS, max(1, max_bytes // 3))
            tail = min(EXECUTE_OUTPUT_TAIL_CHARS, max(1, max_bytes // 3))
            omitted = len(output) - head - tail
            output = (
                output[:head]
                + f"\n\n... Output truncated at {max_bytes} bytes; "
                f"kept first {head} and last {tail} characters, "
                f"omitted {omitted} characters. ...\n\n"
                + output[-tail:]
            )
            truncated = True
        if result.returncode != 0:
            output = f"{output.rstrip()}\n\nExit code: {result.returncode}"
        return ExecuteResponse(
            output=output,
            exit_code=result.returncode,
            truncated=truncated,
        )
    except Exception as e:  # noqa: BLE001
        return ExecuteResponse(
            output=f"Error executing command ({type(e).__name__}): {e}",
            exit_code=1,
            truncated=False,
        )
