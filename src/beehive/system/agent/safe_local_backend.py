"""策略注入的原子文件写入、快照编辑和本机执行后端。"""
from __future__ import annotations
import os
import tempfile
from threading import RLock
from pathlib import Path
from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import EditResult, WriteResult
from beehive.common.io.safe_io import replace_with_retry
from beehive.system.agent.backend_paths import normalize_agent_path
from beehive.system.agent.shell_execute import execute_shell, EXECUTE_OUTPUT_MAX_BYTES

def _write_result_path(file_path: str, warning: str | None) -> str:
    """把写入后的非阻断告警附加到成功结果，供文件工具返回给 Agent。"""
    if not warning:
        return file_path
    return f"{file_path}（已写入；警告：{warning}）"

class SafeLocalBackend(LocalShellBackend):
    # 跨后端实例复用可重入锁，覆盖读取快照、替换和最终提交。
    # shortcut: 进程共享锁，确认写入并发成为瓶颈后再评估按路径加锁。
    _file_lock = RLock()

    def __init__(self, *args, path_guard=None, command_guard=None, denied_message="",
                 max_output_bytes=EXECUTE_OUTPUT_MAX_BYTES, **kwargs):
        super().__init__(*args, max_output_bytes=max_output_bytes, **kwargs)
        self._path_guard = path_guard
        self._command_guard = command_guard
        self._denied_message = denied_message

    def _path_denied(self, path):
        return bool(self._path_guard and self._path_guard(path, getattr(self, "root_dir", None)))

    def execute(self, command: str, *, timeout: int | None = None):
        return execute_shell(self, command, timeout=timeout)

    def _coerce_path(self, path: str | None) -> str | None:
        if path is None:
            return None
        return normalize_agent_path(path, getattr(self, "cwd", None))

    def write(self, file_path: str, content: str) -> WriteResult:
        file_path = self._coerce_path(file_path) or file_path
        if self._path_denied(file_path):
            return WriteResult(error=self._denied_message)
        temporary = None
        warning = None
        try:
            with self._file_lock:
                target = self._resolve_path(file_path)
                if self._path_denied(str(target)):
                    return WriteResult(error=self._denied_message)
                validator = getattr(self, "write_validator", None)
                if validator is not None:
                    warning = validator(target, content)
                # 先完成编码，避免无效 Unicode 导致原文件被清空。
                payload = content.encode("utf-8")
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".wokbee-", delete=False) as f:
                    temporary = Path(f.name)
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
                if target.exists():
                    os.chmod(temporary, target.stat().st_mode)
                replace_with_retry(temporary, target)
            return WriteResult(path=_write_result_path(file_path, warning))
        except (OSError, ValueError, RuntimeError) as exc:
            return WriteResult(error=f"写入失败，原文件未被截断：{exc}")
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass  # 清理失败不改变已成功替换的结果。

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        file_path = self._coerce_path(file_path) or file_path
        if self._path_denied(file_path):
            return EditResult(error=self._denied_message)
        from deepagents.backends.utils import perform_string_replacement

        try:
            with self._file_lock:
                target = self._resolve_path(file_path)
                if self._path_denied(str(target)):
                    return EditResult(error=self._denied_message)
                content = target.read_text(encoding="utf-8")
                old_string = old_string.replace("\r\n", "\n").replace("\r", "\n")
                new_string = new_string.replace("\r\n", "\n").replace("\r", "\n")
                result = perform_string_replacement(content, old_string, new_string, replace_all)
                if isinstance(result, str):
                    return EditResult(error=result)
                updated, occurrences = result
                written = self.write(file_path, updated)
                return EditResult(
                    error=written.error,
                    path=written.path or file_path,
                    occurrences=occurrences,
                )
        except (OSError, ValueError, RuntimeError) as exc:
            return EditResult(error=f"编辑失败：{exc}")
