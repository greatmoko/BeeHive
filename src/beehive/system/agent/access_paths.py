"""主机路径识别、规范化和已授权目录路由。"""
from __future__ import annotations
import os
import re

_DRIVE_RE = re.compile(r"^[a-zA-Z]:")  # C:foo / C:\foo（含 drive-relative C:foo）
_UNC_RE = re.compile(r"^\\\\")          # \\server\share（UNC）
_GUIDE_MSG = (
    "错误：你传给文件工具的路径「{path}」看起来是真实主机路径，但该目录未授权。"
    "文件工具只接受虚拟路径：项目内请用 workspace/…、output/…、uploads/… 等；"
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

def coerce_real(registry, path: str) -> str | None:
    """主机真实路径 → /ext/<slug>/…；无匹配返回 None。"""
    if registry is None:
        return None
    norm = _normalize_host(path)
    # realpath 解析 junction/符号链接与 8.3 短名，避免「进了已批目录却因路径写法不同报未授权」
    try:
        n = os.path.normcase(os.path.realpath(norm))
    except (OSError, ValueError):
        n = os.path.normcase(os.path.normpath(norm))
    for e in registry.entries():
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

def route_prefix_for(registry, path: str | None) -> str | None:
    """若 path 落在某 /ext/<slug>/ 路由下，返回该路由前缀（带尾斜杠）；否则 None。"""
    if registry is None or not path:
        return None
    p = str(path).replace("\\", "/")
    best = None
    for e in registry.entries():
        prefix = str(e["prefix"] or "")
        if not prefix.endswith("/"):
            prefix += "/"
        if p.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return best
