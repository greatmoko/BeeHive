"""强制文件工具使用虚拟路径，并对已获批的真实路径做自动改写。

背景：文件工具实际有两道门。
1. deepagents 的 `validate_path`（middleware/filesystem.py）已拒绝真正的 Windows 绝对路径
   `C:\\...`（utils.py:687），所以真实的 `C:\\...` 根本到不了后端。
2. 但 Agent 常把真实路径**前面加 `/`** 写成 `/C:/Users/...` —— 这种通过 validate_path，
   会到达后端。本包装负责收尾：把「已获批附加目录」里的真实路径**自动改写**成
   `/ext/<slug>/…`，未获批的真实路径则返回**教学式错误**，引导其走 request_access。

处理表（`_prep` 返回值成对：`(error, use_path)`）：
- 非主机路径（workspace/…、/ext/<slug>/…、/skills/…、相对虚拟路径）→ 原样透传；
- 主机路径，且在某个已获批目录下 → 自动改写为 `/ext/<slug>/<相对>`，直接成功；
- 主机路径，但未获批 → 返回教学式错误（教 Agent 去 request_access）；
- `execute` 原样透传（本就允许真实路径）。

`SandboxBackendProtocol` 的 async 方法都经 `asyncio.to_thread(self.<sync>, ...)` 委托
（protocol.py），因此只需覆写 sync 方法即可同步覆盖 async；图用 `agent.stream`（sync）跑。
"""

from __future__ import annotations

import inspect
import os
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

_DRIVE_RE = re.compile(r"^[a-zA-Z]:")  # C:foo / C:\foo（含 drive-relative C:foo）
_UNC_RE = re.compile(r"^\\\\")          # \\server\share（UNC）
_JSON_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")
_INLINE_FILE_PROBE_RE = re.compile(
    r"(?is)(?:node(?:\.exe)?|python(?:\.exe)?|powershell(?:\.exe)?)\b.*?"
    r"(?:\s-e\s|\s-command\s).*?"
    r"(?:readFileSync|read_text|Get-Content|open\s*\().*?"
    r"(?:split\s*\(|readlines\s*\(|Select-Object|for\s*\()"
)
_TEMP_FILE_HELPER_RE = re.compile(
    r"(?i)(?:^|[\\/])_?(?:ln|scan)\d+\.(?:py|js|ps1)\b"
)

_GUIDE_MSG = (
    "错误：你传给文件工具的路径「{path}」看起来是真实主机路径，但该目录未授权。"
    "文件工具只接受虚拟路径：项目内请用 workspace/…、deliverables/…、uploads/… 等；"
    "项目外请先调用 request_access(path, reason) 申请加入白名单，"
    "获批后用返回的 /ext/<slug>/… 虚拟路径。只有 execute 才接受真实路径。"
)


def _is_host_path(p: str | None) -> bool:
    """判断是否像真实主机路径（含 drive-relative `C:foo` 与 UNC `\\server\\share`）。

    用于把「主机路径」从「项目内虚拟路径」里区分出来，从而走归一化/授权检查，
    而不是被当作虚拟路径静默路由到项目根（否则 `C:foo` 会错变成 `项目根/C:foo`）。
    """
    if not p:
        return False
    s = str(p)
    while s.startswith("/"):
        s = s[1:]
    return bool(_DRIVE_RE.match(s) or _UNC_RE.match(s))


def _decode_json_unicode_escapes(text: str) -> str:
    """只解一层 ``\\uXXXX``，供 edit_file 的双重转义兼容回退使用。"""
    return _JSON_UNICODE_ESCAPE_RE.sub(
        lambda match: chr(int(match.group(1), 16)), text
    )


# 按 backend 类型缓存 grep 签名，避免每次 grep 都 inspect.signature 内省。
_GREP_CONTEXT_CACHE: dict[type, bool] = {}


def _grep_accepts_context_lines(backend) -> bool:
    """内层 backend.grep 是否收 context_lines。

    deepagents 单个 FilesystemBackend/LocalShellBackend 收，但聚合的
    CompositeBackend.grep 不收。透传前用签名探测，避免把 context_lines
    传给 CompositeBackend 触发 `got an unexpected keyword argument`。
    签名由类决定，故按类型缓存探针结果。
    """
    cls = type(backend)
    if cls not in _GREP_CONTEXT_CACHE:
        try:
            _GREP_CONTEXT_CACHE[cls] = "context_lines" in inspect.signature(backend.grep).parameters
        except (TypeError, ValueError):
            _GREP_CONTEXT_CACHE[cls] = False
    return _GREP_CONTEXT_CACHE[cls]


def _normalize_host(path: str) -> str:
    """把 `/C:/x`、`C:\\x`、`C:x`、`\\server\\share` 统一成规范 Windows 路径。

    drive-relative（`C:foo`）无法确定基准目录，保持原样——它不会命中任何已授权的绝对目录，
    自然落入「未授权」教学错误，符合先拒绝原则。
    """
    p = str(path)
    while p.startswith("/"):
        p = p[1:]
    try:
        return os.path.normpath(p)
    except (OSError, ValueError):
        return p


class AccessCoerceBackend(SandboxBackendProtocol):
    """CompositeBackend 外层：强制虚拟路径 + 已获批真实路径自动改写 + execute 透传。"""

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

    def _coerce_real(self, path: str) -> str | None:
        """主机真实路径 → /ext/<slug>/…；无匹配返回 None。"""
        if self._registry is None:
            return None
        norm = _normalize_host(path)
        # realpath 解析 junction/符号链接与 8.3 短名，避免「进了已批目录却因路径写法不同报未授权」
        try:
            n = os.path.normcase(os.path.realpath(norm))
        except (OSError, ValueError):
            n = os.path.normcase(os.path.normpath(norm))
        for e in self._registry.entries():
            try:
                real = os.path.normcase(os.path.realpath(e["real_dir"]))
            except (OSError, ValueError):
                real = os.path.normcase(os.path.normpath(e["real_dir"]))
            if n == real:
                return e["prefix"]
            sep = os.sep
            if n.startswith(real + sep):
                rel = n[len(real) + 1 :]
                return e["prefix"] + rel.replace(sep, "/")
        return None

    def _prep(self, path: str | None) -> tuple[str | None, str | None]:
        """返回 (error, use_path)。error 非空则直接用该错误；否则 use_path 是实际路径。"""
        if not path:
            return None, path
        p = str(path)
        if not _is_host_path(p):
            return None, p  # 非主机路径（虚拟/相对） → 透传
        if self.allow_real_paths:
            return None, p  # 忽略沙箱：真实路径原样透传，不改写、不教学式拦截
        coerced = self._coerce_real(p)
        if coerced is not None:
            return None, coerced  # 已获批 → 自动改写为 /ext/<slug>/…
        return _GUIDE_MSG.format(path=p), p  # 未获批 → 教学式错误

    def _route_prefix_for(self, path: str | None) -> str | None:
        """若 path 落在某 /ext/<slug>/ 路由下，返回该路由前缀（带尾斜杠）；否则 None。"""
        if self._registry is None or not path:
            return None
        p = str(path).replace("\\", "/")
        best = None
        for e in self._registry.entries():
            prefix = str(e["prefix"] or "")
            if not prefix.endswith("/"):
                prefix += "/"
            if p.startswith(prefix) and (best is None or len(prefix) > len(best)):
                best = prefix
        return best

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
        rp = self._route_prefix_for(use_path)
        if rp:
            pp = p.replace("\\", "/")
            if pp.startswith(rp):
                p = pp[len(rp) :]
        return p, None

    # ---- file tools：先 _prep（改写到 /ext/ 或拦下），再转内层 ----
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

    # ---- upload/download：SandboxBackendProtocol 接口要求实现，但当前没有暴露给 Agent 的
    #      对应工具，属纯透传（middleware 已校验）。保留以保证接口完整，勿删除。----
    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self._inner.upload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self._inner.download_files(paths)

    # ---- execute：真实交付物可运行；文件浏览脚本只在文件工具失败后兜底一次。----
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
