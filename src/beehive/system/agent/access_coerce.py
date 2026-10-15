"""文件接口适配：统一授权路径检查、编辑兼容和脚本兜底限制。"""
from __future__ import annotations
import re
from pathlib import Path
from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)
from beehive.system.agent.access_paths import _is_host_path, _GUIDE_MSG, coerce_real, route_prefix_for
from beehive.system.agent.file_compat import _decode_json_unicode_escapes, _grep_accepts_context_lines

_INLINE_FILE_PROBE_RE = re.compile(
    r"(?is)(?:node(?:\.exe)?|python(?:\.exe)?|powershell(?:\.exe)?)\b.*?"
    r"(?:\s-e\s|\s-command\s).*?"
    r"(?:readFileSync|read_text|Get-Content|open\s*\().*?"
    r"(?:split\s*\(|readlines\s*\(|Select-Object|for\s*\()"
)
_TEMP_FILE_HELPER_RE = re.compile(
    r"(?i)(?:^|[\\/])_?(?:ln|scan)\d+\.(?:py|js|ps1)\b"
)


class AccessCoerceBackend(SandboxBackendProtocol):
    def __init__(
        self,
        backend,
        *,
        project_root: str | Path | None = None,
        registry=None,
        allow_real_paths: bool = False,
    ):
        self._inner = backend
        self._project_root = str(Path(project_root).resolve()) if project_root else None
        self._registry = registry
        self.allow_real_paths = bool(allow_real_paths)
        self._file_tool_failed = False
        self._file_probe_fallbacks = 0

    def _remember_file_error(self, result) -> None:
        if getattr(result, "error", None):
            self._file_tool_failed = True

    @property
    def id(self) -> str:
        return getattr(self._inner, "id", str(type(self._inner).__name__))

    def _prep(self, path: str | None) -> tuple[str | None, str | None]:
        """返回 (error, use_path)。error 非空则直接用该错误；否则 use_path 是实际路径。"""
        if not path:
            return None, path
        p = str(path)
        if not _is_host_path(p):
            return None, p  # 非主机路径（虚拟/相对） → 透传
        if self.allow_real_paths:
            return None, p  # 忽略沙箱：真实路径原样透传，不改写、不教学式拦截
        coerced = coerce_real(self._registry, p)
        if coerced is not None:
            return None, coerced  # 已获批 → 自动改写为 /ext/<slug>/…
        return _GUIDE_MSG.format(path=p), p  # 未获批 → 教学式错误

    def _normalize_glob_pattern(self, pattern, use_path) -> tuple[str | None, str | None]:
        """glob/grep 的 pattern/glob 参数归一化。返回 (norm_pattern, error)。

        解决两类问题：
        - 带通配的主机模式（如 C:\\Users\\*\\*.md）此前因含通配符而跳过 _prep，
          未获批也直接进内层搜——现在仍按主机路径走归一化/授权，未获批返回教学错误；
        - path 与 pattern 都带 /ext/<slug>/ 前缀时，composite 的 routed 分支不剥前缀，
          返回空——这里把 pattern 的路由前缀剥掉再传。
        """
        if not pattern:
            return pattern, None
        p = str(pattern)
        if _is_host_path(p):
            err, coerced = self._prep(p)
            if err:
                return None, err
            p = coerced
        rp = route_prefix_for(self._registry, use_path)
        if rp:
            pp = p.replace("\\", "/")
            if pp.startswith(rp):
                p = pp[len(rp) :]
        return p, None

    def ls(self, path: str) -> LsResult:
        err, use_path = self._prep(path)
        if err:
            return LsResult(error=err)
        return self._inner.ls(use_path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        err, use_path = self._prep(file_path)
        if err:
            result = ReadResult(error=err)
        else:
            result = self._inner.read(use_path, offset=offset, limit=limit)
        self._remember_file_error(result)
        return result

    def write(self, file_path: str, content: str) -> WriteResult:
        err, use_path = self._prep(file_path)
        if err:
            result = WriteResult(error=err)
        else:
            result = self._inner.write(use_path, content)
        self._remember_file_error(result)
        return result

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        err, use_path = self._prep(file_path)
        if err:
            result = EditResult(error=err)
            self._remember_file_error(result)
            return result
        result = self._inner.edit(use_path, old_string, new_string, replace_all=replace_all)
        error = str(getattr(result, "error", "") or "")
        if not error or "String not found in file" not in error:
            self._remember_file_error(result)
            return result

        # 部分模型会把已是 JSON 字符串参数的 ``<`` 再编码成 ``\\u003c``。
        # 仅在原文本未命中、解码后恰好命中一次时回退，绝不做模糊匹配。
        decoded = _decode_json_unicode_escapes(old_string)
        if decoded == old_string:
            self._remember_file_error(result)
            return result
        try:
            read_result = self._inner.read(use_path, offset=0, limit=100_000_000)
            data = getattr(read_result, "file_data", None) or {}
            content = str(data.get("content") or "")
        except Exception:
            self._remember_file_error(result)
            return result
        if getattr(read_result, "error", None):
            self._remember_file_error(result)
            return result
        if content.count(decoded) != 1:
            result = EditResult(
                error=(
                    f"{error}\n检测到 old_string 含 JSON Unicode 转义，解码后在文件中出现 "
                    f"{content.count(decoded)} 次；为避免误改未自动替换。请先用 read_file_range "
                    "确认当前区域，再用 insert_text 的唯一纯文本锚点操作。"
                )
            )
            self._remember_file_error(result)
            return result
        result = self._inner.edit(use_path, decoded, new_string, replace_all=replace_all)
        self._remember_file_error(result)
        return result

    def delete(self, file_path: str) -> DeleteResult:
        err, use_path = self._prep(file_path)
        if err:
            return DeleteResult(error=err)
        return self._inner.delete(use_path)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        err, use_path = self._prep(path)
        if err:
            return GlobResult(error=err)
        npat, perr = self._normalize_glob_pattern(pattern, use_path)
        if perr:
            return GlobResult(error=perr)
        return self._inner.glob(npat, use_path)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
        context_lines: int = 0,
    ) -> GrepResult:
        err, use_path = self._prep(path)
        if err:
            return GrepResult(error=err)
        if glob is not None:
            nglob, gerr = self._normalize_glob_pattern(glob, use_path)
            if gerr:
                return GrepResult(error=gerr)
            glob = nglob
        if _grep_accepts_context_lines(self._inner):
            return self._inner.grep(
                pattern, use_path, glob, max_count=max_count, context_lines=context_lines
            )
        return self._inner.grep(pattern, use_path, glob, max_count=max_count)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self._inner.upload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self._inner.download_files(paths)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        is_file_probe = bool(
            _INLINE_FILE_PROBE_RE.search(command or "")
            or _TEMP_FILE_HELPER_RE.search(command or "")
        )
        if is_file_probe:
            if not self._file_tool_failed:
                return ExecuteResponse(
                    output=(
                        "已拒绝：不要通过 execute 用 Node/Python/PowerShell 分段读取或搜索文件。"
                        "请直接使用 find_in_file 定位，再用 read_file 或 read_file_range 读取上下文。"
                        "只有这些文件工具实际返回错误、重新定位一次仍失败后，才允许一次脚本兜底。"
                    ),
                    exit_code=1,
                    truncated=False,
                )
            if self._file_probe_fallbacks >= 1:
                return ExecuteResponse(
                    output=(
                        "已停止重复脚本兜底：本轮已执行过一次文件浏览脚本，继续不会产生新信息。"
                        "请回到 find_in_file/read_file_range/insert_text；若仍不能完成，应报告具体文件工具错误。"
                    ),
                    exit_code=1,
                    truncated=False,
                )
            self._file_probe_fallbacks += 1
        return self._inner.execute(command, timeout=timeout)
