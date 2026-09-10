r"""Windows 扩展路径（\\?\）兼容：修正 deepagents 误判「越界」。

背景（真实故障）：`FilesystemBackend._resolve_path` 在 `virtual_mode` 下用
`full = (cwd / vpath).resolve()` 再 `full.relative_to(self.cwd)` 做包含性校验。
Windows 上 `Path.resolve()` 走 `_getfinalpathname`，当目标文件正被并发创建
（Agent 并行 write_file）时，Python 会在个别次调用里保留 `\\?\C:\…` 扩展前缀
（`_getfinalpathname(spath) == path` 的复核失败即不回退）。此时字符串比较：

    \\?\C:\…\deliverables\prd\x.md   ← full
    C:\…                             ← cwd

判定为「不在根目录内」，于是本轮对话直接失败：

    交互失败：Path:\\?\C:\…\x.md outside root directory: C:\…

修复：包含性校验前剥掉扩展前缀（含 UNC 形式），仍按真实路径比较；确属越界或
`..` 穿越的路径照旧拒绝。仅在 `virtual_mode` 下介入，非虚拟模式原样委托。
"""

from __future__ import annotations

from pathlib import Path

_EXT = "\\\\?\\"
_EXT_UNC = "\\\\?\\UNC\\"


def strip_extended_prefix(path: str | Path) -> str:
    r"""剥掉 Windows `\\?\` 扩展前缀（UNC 折叠回 `\\server\share`）。"""
    s = str(path)
    if s.startswith(_EXT_UNC):
        return "\\\\" + s[len(_EXT_UNC):]
    if s.startswith(_EXT):
        return s[len(_EXT):]
    return s


def _is_within(path: Path, root: Path) -> bool:
    """path 是否在 root 内（大小写不敏感、忽略扩展前缀）。"""
    try:
        Path(strip_extended_prefix(path)).relative_to(
            Path(strip_extended_prefix(root))
        )
    except ValueError:
        return False
    return True


def install_extended_path_tolerance() -> None:
    r"""幂等补丁：让 deepagents 的虚拟路径校验容忍 `\\?\` 前缀。"""
    from deepagents.backends import filesystem as _fs

    backend = _fs.FilesystemBackend
    if getattr(backend, "_wokbee_ext_path_patched", False):
        return

    orig_resolve = backend._resolve_path
    symlink_loop_check = getattr(_fs, "_raise_if_symlink_loop", None)

    def _resolve_path(self, key: str) -> Path:
        if not getattr(self, "virtual_mode", False):
            return orig_resolve(self, key)
        vpath = key if key.startswith("/") else "/" + key
        if ".." in vpath or vpath.startswith("~"):
            raise ValueError("Path traversal not allowed")
        full = (self.cwd / vpath.lstrip("/")).resolve()
        if not _is_within(full, self.cwd):
            raise ValueError(f"Path:{full} outside root directory: {self.cwd}")
        if symlink_loop_check is not None:
            symlink_loop_check(full)
        # 返回去掉扩展前缀的路径：与常规 resolve() 结果一致，避免前缀泄漏到下游
        return Path(strip_extended_prefix(full))

    backend._resolve_path = _resolve_path
    backend._wokbee_ext_path_patched = True
