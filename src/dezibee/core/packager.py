"""DeziBee 单文件打包：把 demo/ 的 index.html + css + js + 本地图片/字体
内联成一个**自包含 HTML**，便于直接上传 OSS / 静态托管（只传一个文件）。

产物约束：
 - 零外部请求：css / js / 本地图片字体全部内联为 data URI 或内联标签；
 - 保留 WORKBENCH_DATA 结构与页面行为（画布、联动、PRD 渲染）；
 - 注入 window.__DEZIBEE_EXPORT__ = true，前端据此隐藏「编辑/导出」等
   依赖本地预览服务器的功能，静态页保持纯只读。
"""

from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path

# 整体标签替换：<link ... href=...> / <script ... src=...></script>
_CSS_LINK_RE = re.compile(r'<link\b[^>]*?\bhref\s*=\s*["\']([^"\']+)["\'][^>]*?>', re.IGNORECASE)
_SCRIPT_TAG_RE = re.compile(
    r'<script\b[^>]*?\bsrc\s*=\s*["\']([^"\']+)["\'][^>]*?>\s*</script>', re.IGNORECASE
)

# 资源引用：src="..." / href="..." / css url(...)
# 注意：两个分支都要加 `(?<![\w-])`，否则 JS 里的 createObjectURL(blob) 会被当成 url(blob)
_URL_RE = re.compile(
    r'''(?<![\w-])(?:src|href)\s*=\s*["']([^"']+)["']'''
    r'''|(?<![\w-])url\(\s*["']?([^"')]+?)["']?\s*\)''',
    re.IGNORECASE,
)

# 只有这些扩展名会被内联（避免把 .html / .md 等递归内联进自身）
_INLINE_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".avif",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
}

_NOT_LOCAL_PREFIXES = (
    "http://", "https://", "//", "data:", "blob:", "#", "mailto:", "javascript:", "tel:",
)

_EXPORT_FLAG = "<script>window.__DEZIBEE_EXPORT__=true;</script>"


def _is_local_ref(ref: str) -> bool:
    r = ref.strip()
    if not r or r.startswith("/"):  # 项目规则禁止绝对路径，不处理
        return False
    return not r.lower().startswith(_NOT_LOCAL_PREFIXES)


def _resolve(base_dir: Path, ref: str, root: Path | None = None) -> Path | None:
    """把相对引用解析为真实文件；越界/不存在返回 None。

    base_dir 是引用所在文件的位置（相对它解析，允许 ../）；
    root 是允许访问的边界目录（默认 base_dir），用于防止跳出项目范围。
    """
    ref = ref.split("?", 1)[0].split("#", 1)[0].strip()
    if not _is_local_ref(ref):
        return None
    boundary = (root or base_dir).resolve()
    try:
        p = (base_dir / ref).resolve()
    except (OSError, ValueError):
        return None
    if p != boundary and boundary not in p.parents:
        return None
    return p if p.is_file() else None


def _data_uri(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime or 'application/octet-stream'};base64,{data}"


def _inline_assets(base_dir: Path, text: str, root: Path | None = None) -> str:
    """把本地资源（图片/字体）替换成 data URI；base_dir 为相对解析位置，root 为边界。"""
    refs: set[str] = set()
    for m in _URL_RE.finditer(text):
        ref = (m.group(1) or m.group(2) or "").strip()
        if ref:
            refs.add(ref)
    # 长路径优先，避免较短路径误伤其子串
    for ref in sorted(refs, key=len, reverse=True):
        if Path(ref.split("?", 1)[0]).suffix.lower() not in _INLINE_EXTS:
            continue
        p = _resolve(base_dir, ref, root)
        if p is None:
            continue
        text = text.replace(ref, _data_uri(p))
    return text


def _escape_script(js: str) -> str:
    """避免内联脚本里出现 </script> 提前闭合外层标签。"""
    return re.sub(r"</(script)", r"<\\/\1", js, flags=re.IGNORECASE)


def build_single_file(demo_dir: Path) -> tuple[str, list[str]]:
    """把 demo_dir/index.html 打包成自包含 HTML。

    返回 (html, warnings)：warnings 列出引用了但找不到的本地文件，供上层提示。
    """
    demo_dir = Path(demo_dir)
    index = demo_dir / "index.html"
    if not index.is_file():
        raise FileNotFoundError(f"未找到 {index}")

    text = index.read_text(encoding="utf-8")
    warnings: list[str] = []
    injected_flag = False

    # 1) 内联样式：<link href="css/..."> → <style>；css 内的 url() 相对该 css 文件解析
    def css_sub(m: re.Match) -> str:
        ref = m.group(1)
        p = _resolve(demo_dir, ref)
        if p is None or p.suffix.lower() != ".css":
            warnings.append(f"样式未内联（找不到）：{ref}")
            return m.group(0)
        css = _inline_assets(p.parent, p.read_text(encoding="utf-8"), demo_dir)
        return f"<style>\n{css}\n</style>"

    text = _CSS_LINK_RE.sub(css_sub, text)

    # 2) 内联脚本：<script src="js/..."></script> → <script>
    def js_sub(m: re.Match) -> str:
        nonlocal injected_flag
        ref = m.group(1)
        p = _resolve(demo_dir, ref)
        if p is None or p.suffix.lower() != ".js":
            warnings.append(f"脚本未内联（找不到）：{ref}")
            return m.group(0)
        js = _escape_script(p.read_text(encoding="utf-8"))
        prefix = "" if injected_flag else _EXPORT_FLAG
        injected_flag = True
        return f"{prefix}<script>\n{js}\n</script>"

    text = _SCRIPT_TAG_RE.sub(js_sub, text)

    # 3) 内联卡片/PRD 内容里引用的本地图片（相对 demo/ 根；iframe 继承页面基址）
    text = _inline_assets(demo_dir, text)

    # 4) 收尾体检：仍残留的本地引用会随单文件上传后失效，明确告警
    remaining: set[str] = set()
    for m in _URL_RE.finditer(text):
        ref = (m.group(1) or m.group(2) or "").strip()
        if ref and _is_local_ref(ref):
            remaining.add(ref)
    for ref in sorted(remaining):
        if ref not in text:  # 已被 data URI 替换掉
            continue
        warnings.append(
            f"未内联的本地引用：{ref}（单文件上传后可能 404）"
            if _resolve(demo_dir, ref, demo_dir) is None
            else f"未内联的本地引用：{ref}（该类型不自动内联）"
        )

    return text, warnings
