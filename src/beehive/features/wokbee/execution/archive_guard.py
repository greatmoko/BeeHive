"""项目归档访问守卫及文件查询结果过滤。"""
from __future__ import annotations
from pathlib import PurePosixPath
from deepagents.backends.protocol import DeleteResult, GlobResult, GrepResult, LsResult, ReadResult
from beehive.system.agent.safe_local_backend import SafeLocalBackend
from beehive.system.agent.backend_paths import normalize_agent_path
from beehive.system.agent.shell_execute import EXECUTE_OUTPUT_MAX_BYTES, EXECUTE_OUTPUT_HEAD_CHARS, EXECUTE_OUTPUT_TAIL_CHARS
from beehive.system.agent.execute_watch import attach_execute_watch
from beehive.features.wokbee.execution.archive_policy import _DENY_MSG, path_touches_archives, command_touches_archives

class ArchiveDeniedBackend(SafeLocalBackend):
    """项目归档策略适配；共享后端不查询项目存储。"""
    def __init__(self, *args, max_output_bytes=EXECUTE_OUTPUT_MAX_BYTES, **kwargs):
        super().__init__(*args, max_output_bytes=max_output_bytes,
                         path_guard=path_touches_archives, command_guard=command_touches_archives,
                         denied_message=_DENY_MSG, **kwargs)

    def _archive_hit(self, path: str | None) -> bool:
        """按本项目根限定归档边界（不是任何名字含 archives 的路径）。"""
        return path_touches_archives(path, getattr(self, "root_dir", None))

    def ls(self, path: str) -> LsResult:
        path = self._coerce_path(path) or path
        if self._archive_hit(path):
            return LsResult(error=_DENY_MSG)
        result = super().ls(path)
        if result.error or not result.entries:
            return result
        filtered = []
        for e in result.entries:
            p = ""
            if isinstance(e, dict):
                p = str(e.get("path") or "")
                name = PurePosixPath(p.replace("\\", "/")).name if p else ""
            else:
                p = str(getattr(e, "path", "") or "")
                name = PurePosixPath(p.replace("\\", "/")).name if p else str(getattr(e, "name", "") or "")
            if name.lower() == "archives" or self._archive_hit(p):
                continue
            filtered.append(e)
        return LsResult(error=None, entries=filtered)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        file_path = self._coerce_path(file_path) or file_path
        if self._archive_hit(file_path):
            return ReadResult(error=_DENY_MSG)
        return super().read(file_path, offset=offset, limit=limit)

    def delete(self, file_path: str) -> DeleteResult:
        file_path = self._coerce_path(file_path) or file_path
        if self._archive_hit(file_path):
            return DeleteResult(error=_DENY_MSG)
        return super().delete(file_path)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        path = self._coerce_path(path)
        pattern = self._coerce_path(pattern) or pattern
        if self._archive_hit(path) or self._archive_hit(pattern):
            return GlobResult(error=_DENY_MSG)
        result = super().glob(pattern, path)
        if result.error or not result.matches:
            return result
        matches = []
        for m in result.matches:
            p = str(m.get("path") if isinstance(m, dict) else getattr(m, "path", m) or "")
            if not self._archive_hit(p):
                matches.append(m)
        return GlobResult(
            error=None,
            matches=matches,
            truncated=bool(getattr(result, "truncated", False)),
        )

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
        context_lines: int = 0,
    ) -> GrepResult:
        path = self._coerce_path(path)
        if self._archive_hit(path) or self._archive_hit(glob):
            return GrepResult(error=_DENY_MSG)
        result = super().grep(
            pattern,
            path,
            glob,
            max_count=max_count,
            context_lines=context_lines,
        )
        if result.error or not result.matches:
            return result
        matches = []
        for m in result.matches:
            p = str(m.get("path") if isinstance(m, dict) else getattr(m, "path", "") or "")
            if not self._archive_hit(p):
                matches.append(m)
        return GrepResult(
            error=None,
            matches=matches,
            truncated=bool(getattr(result, "truncated", False)),
        )
