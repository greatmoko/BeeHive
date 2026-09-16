"""DeziBee Preview Server：静态文件服务 + 系统默认浏览器打开。

按需求文档第二节「不要在 WokBee 内嵌浏览器」「调用系统默认浏览器预览」：
 - 用 Python 标准库 http.server（ThreadingHTTPServer + SimpleHTTPRequestHandler）
   静态目录服务，零新增依赖；
 - 绑定 127.0.0.1（仅本机），滚动可用端口（默认 38681 起）；
 - 每个需求 URL：http://127.0.0.1:<port>/<需求ID>/
 - 打开浏览器复用项目现有能力：dialogs.open_path / webbrowser.open。
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logger = logging.getLogger("dezibee")

_DEFAULT_PORT = 38681
_SERVER: _PreviewServer | None = None
_LOCK = threading.Lock()

# 预览页内直接编辑 PRD 的保存端点（仅本机预览服务器提供；静态部署无此端点 → 只读）
_PRD_ENDPOINT = "/__dezibee__/prd"
# 预览页内导出单文件 HTML 的端点（下载自包含 HTML，便于直接上传 OSS）
_EXPORT_ENDPOINT = "/__dezibee__/export"
_MAX_BODY = 20 * 1024 * 1024  # 20MB 上限


class _BadRequest(Exception):
    """请求参数不合法（回 400，而不是 500）。"""


# ── WORKBENCH_DATA.prd 定点改写 ─────────────────────────────────────
# 用字符串/模板/注释感知的括号配对，只替换 `prd` 的值，其余 pages/links/格式原样保留。

_JS_PAIRS = {"{": "}", "[": "]"}


def validate_workbench_document(content: str) -> None:
    """Reject truncated workbench documents; this is not a full JS syntax checker."""
    from html.parser import HTMLParser

    class Document(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.ends = set()
            self.in_script = False
            self.scripts = []

        def handle_starttag(self, tag, attrs):
            if tag == "script":
                self.in_script = True

        def handle_endtag(self, tag):
            self.ends.add(tag)
            if tag == "script":
                self.in_script = False

        def handle_data(self, data):
            if self.in_script:
                self.scripts.append(data)

    document = Document()
    document.feed(content)
    document.close()
    if document.in_script or not {"body", "html"} <= document.ends:
        raise ValueError("原型 HTML 未完整闭合，拒绝覆盖。请补齐 script/body/html 后重新提交。")
    for script in document.scripts:
        match = re.search(r"\bWORKBENCH_DATA\s*=\s*(?:window\.WORKBENCH_DATA\s*=\s*)?\{", script)
        if match and _match_bracket(script, match.end() - 1) is not None:
            return
    raise ValueError("WORKBENCH_DATA 缺失或括号/字符串未闭合，拒绝覆盖原型。")


def _skip_js_string(text: str, i: int) -> int:
    """i 位于引号（' " `）处；返回字符串结束后的下标。模板串会跳过 ${...}。"""
    quote = text[i]
    n = len(text)
    i += 1
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return i + 1
        if quote == "`" and c == "$" and i + 1 < n and text[i + 1] == "{":
            j = _match_bracket(text, i + 1)
            i = n if j is None else j + 1
            continue
        i += 1
    return n


def _match_bracket(text: str, open_idx: int) -> int | None:
    """open_idx 指向 '{' 或 '['；返回配对闭合下标（跳过字符串与注释）。"""
    stack = [_JS_PAIRS[text[open_idx]]]
    i = open_idx + 1
    n = len(text)
    while i < n:
        c = text[i]
        if c in "\"'`":
            i = _skip_js_string(text, i)
            continue
        if c == "/" and text.startswith("//", i):
            j = text.find("\n", i + 2)
            i = n if j < 0 else j + 1
            continue
        if c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in _JS_PAIRS:
            stack.append(_JS_PAIRS[c])
        elif stack and c == stack[-1]:
            stack.pop()
            if not stack:
                return i
        i += 1
    return None


def _find_toplevel_key(text: str, obj_open: int, obj_close: int, key: str):
    """在对象字面量内找顶层 key，返回 (key起始, 冒号起始, 值起始)；找不到返回 None。"""
    i = obj_open + 1
    depth = 0
    klen = len(key)
    colon_re = re.compile(r"\s*:")
    while i < obj_close:
        c = text[i]
        if c in "\"'`":
            i = _skip_js_string(text, i)
            continue
        if c == "/" and text.startswith("//", i):
            j = text.find("\n", i + 2)
            i = len(text) if j < 0 else j + 1
            continue
        if c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = len(text) if j < 0 else j + 2
            continue
        if c in _JS_PAIRS:
            depth += 1
            i += 1
            continue
        if c in "}]":
            depth -= 1
            i += 1
            continue
        if depth == 0 and text.startswith(key, i):
            prev = text[i - 1] if i > 0 else ""
            m = colon_re.match(text, i + klen)
            if not (prev.isalnum() or prev in "_$") and m:
                v = m.end()
                while v < len(text) and text[v] in " \t\r\n":
                    v += 1
                return i, m.start(), v
        i += 1
    return None


def _rewrite_prd_in_index(index_path: Path, prd_obj: dict) -> None:
    """把 index.html 中 WORKBENCH_DATA.prd 的值替换为 prd_obj（其余内容原样保留）。

    如此用户手改的 PRD 与 AI 编辑的是同一份真源（demo/index.html）。
    """
    text = index_path.read_text(encoding="utf-8")
    m = re.search(
        r"WORKBENCH_DATA\s*=\s*(?:window\.WORKBENCH_DATA\s*=\s*)?\{", text
    )
    if not m:
        raise _BadRequest("index.html 中未找到 WORKBENCH_DATA")
    obj_open = m.end() - 1
    obj_close = _match_bracket(text, obj_open)
    if obj_close is None:
        raise _BadRequest("WORKBENCH_DATA 结构不完整")
    found = _find_toplevel_key(text, obj_open, obj_close, "prd")
    if not found:
        raise _BadRequest("WORKBENCH_DATA 中未找到 prd")
    key_idx, _colon, val_idx = found
    if text[val_idx] != "{":
        raise _BadRequest("WORKBENCH_DATA.prd 不是对象")
    val_end = _match_bracket(text, val_idx)
    if val_end is None:
        raise _BadRequest("WORKBENCH_DATA.prd 结构不完整")

    # 按 prd 所在行的缩进对齐序列化结果，保持文件可读
    line_start = text.rfind("\n", 0, key_idx) + 1
    indent = text[line_start:key_idx]
    if indent.strip():
        indent = ""
    # `<` 转成 \u003c：JSON 语义不变，但避免内容里的 </script> 提前闭合外层 script 标签
    new_val = (
        json.dumps(prd_obj, ensure_ascii=False, indent=2)
        .replace("<", "\\u003c")
        .replace("\n", "\n" + indent)
    )
    new_text = text[:val_idx] + new_val + text[val_end + 1:]

    tmp = index_path.with_name(index_path.name + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, index_path)


def _resolve_demo_index(root: Path, req_id) -> Path:
    """校验 req_id 并返回 <root>/<req_id>/demo/index.html（防目录穿越）。"""
    if not isinstance(req_id, str) or not req_id.strip():
        raise _BadRequest("缺少 req_id")
    if req_id.strip() != req_id or any(s in req_id for s in ("/", "\\", "\x00")) or ".." in req_id:
        raise _BadRequest("req_id 非法")
    root_res = Path(root).resolve()
    req_dir = (root_res / req_id).resolve()
    if req_dir != root_res and root_res not in req_dir.parents:
        raise _BadRequest("req_id 越界")
    index = req_dir / "demo" / "index.html"
    if not index.is_file():
        raise _BadRequest(f"未找到 {req_id}/demo/index.html")
    return index


def _find_free_port(start: int, tries: int = 40) -> int:
    """从 start 起找第一个可绑定的端口。"""
    port = start
    for _ in range(tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                port += 1
    return 0


class _Handler(SimpleHTTPRequestHandler):
    """安静版静态处理器：不往 stdout 打日志。

    统一 no-cache：css/js 等框架文件随应用升级会覆盖，浏览器每次需回源校验
    （未变更回 304），避免升级后仍用旧缓存的框架文件。
    """

    def log_message(self, format, *args):  # noqa: A002
        return

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


class _PreviewServer:
    """在后台线程运行 ThreadingHTTPServer。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        port = _find_free_port(_DEFAULT_PORT)
        if not port:
            raise RuntimeError("找不到可用端口（38681+ 全部被占用）")
        self.port = port
        self._httpd = ThreadingHTTPServer(
            ("127.0.0.1", port),
            _make_handler(self.root),
        )
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name=f"dezibee-preview-{port}",
            daemon=True,
        )

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def shutdown(self) -> None:
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        except Exception:
            pass

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _make_handler(root: Path):
    """构造绑定 root 的处理器。

    访问 `/{需求ID}/` 或 `/{需求ID}` 时：若该需求目录下存在 demo/index.html，
    用 HTTP 302 跳到 `/{需求ID}/demo/index.html`（预览直达 Demo 首页，且让浏览器
    以 demo/ 为基址解析 css/js 等相对资源）；否则正常静态文件/目录列出。

    注意：这里必须是真实重定向而不是内部改写路径——内部改写会让浏览器仍以
    `/{需求ID}/` 为基址，相对引用的 `css/workbench.css`、`js/workbench.js`
    会解析到不存在的 `/{需求ID}/css/...` 而 404，导致页面无样式、无内容。
    """

    class _RootedHandler(_Handler):
        def _demo_redirect_target(self) -> str | None:
            """一层目录且含 demo/index.html 时返回跳转目标（绝对路径），否则 None。"""
            import posixpath
            import urllib.parse

            path = urllib.parse.urlsplit(self.path).path
            norm = posixpath.normpath(urllib.parse.unquote(path)).lstrip("/")
            parts = [p for p in norm.split("/") if p not in ("", ".", "..")] if norm else []
            if len(parts) != 1:
                return None
            target = root.joinpath(*parts)
            if target.is_dir() and (target / "demo" / "index.html").is_file():
                return f"/{parts[0]}/demo/index.html"
            return None

        def _redirect_to_demo(self) -> bool:
            dest = self._demo_redirect_target()
            if dest is None:
                return False
            self.send_response(302)
            self.send_header("Location", dest)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True

        def _send_json(self, code: int, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_export(self) -> None:
            """打包当前需求为单文件 HTML，以附件下载返回（可直接上传 OSS）。"""
            import urllib.parse

            from dezibee.core.packager import build_single_file

            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            req_id = (qs.get("req_id") or [""])[0]
            try:
                index = _resolve_demo_index(root, req_id)
                html, warnings = build_single_file(index.parent)
            except _BadRequest as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception("导出单文件失败")
                self._send_json(500, {"ok": False, "error": f"导出失败：{exc}"})
                return
            if warnings:
                logger.warning("导出单文件告警：%s", "；".join(warnings))
            body = html.encode("utf-8")
            filename = f"{req_id or 'prototype'}.html"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            """预览页保存 PRD：写回 <req>/demo/index.html 的 WORKBENCH_DATA.prd。"""
            import urllib.parse

            if urllib.parse.urlsplit(self.path).path != _PRD_ENDPOINT:
                self.send_error(404, "Not Found")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > _MAX_BODY:
                self._send_json(413, {"ok": False, "error": "请求体为空或过大"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                self._send_json(400, {"ok": False, "error": "JSON 解析失败"})
                return
            try:
                index = _resolve_demo_index(root, payload.get("req_id"))
                prd = payload.get("prd")
                if not isinstance(prd, dict) or not isinstance(prd.get("sections"), list):
                    raise _BadRequest("缺少 prd.sections")
                _rewrite_prd_in_index(index, prd)
            except _BadRequest as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception("保存 PRD 失败")
                self._send_json(500, {"ok": False, "error": f"保存失败：{exc}"})
                return
            self._send_json(200, {"ok": True, "path": str(index)})

        def do_GET(self):  # noqa: N802
            import urllib.parse

            if urllib.parse.urlsplit(self.path).path == _EXPORT_ENDPOINT:
                self._serve_export()
                return
            if self._redirect_to_demo():
                return
            super().do_GET()

        def do_HEAD(self):  # noqa: N802
            if self._redirect_to_demo():
                return
            super().do_HEAD()

        def translate_path(self, path: str) -> str:
            import posixpath
            import urllib.parse

            path = urllib.parse.urlsplit(path).path
            norm = posixpath.normpath(urllib.parse.unquote(path)).lstrip("/")
            parts = [p for p in norm.split("/") if p not in ("", ".", "..")] if norm else []
            target = root.joinpath(*parts) if parts else root
            return str(target.resolve())

    return _RootedHandler


def ensure_server() -> _PreviewServer:
    """确保 Preview Server 已启动，返回单例。"""
    global _SERVER
    with _LOCK:
        if _SERVER is None:
            from wokbee.core.settings import WokBeeSettings

            work_root = WokBeeSettings().dezibee_work_root
            _SERVER = _PreviewServer(Path(work_root))
            _SERVER.start()
        return _SERVER


def req_url(req_id: str) -> str:
    """当前需求的本地 URL：http://127.0.0.1:<port>/<需求ID>/"""
    server = ensure_server()
    return f"{server.base_url}/{req_id}/"


def open_in_browser(url: str) -> bool:
    """调用系统默认浏览器打开 URL（复用 webbrowser，失败时用 dialogs.open_path）。"""
    try:
        opened = webbrowser.open(url)
        if opened:
            return True
    except Exception:
        pass
    try:
        from wokbee.ui.dialogs import open_path

        open_path(url)
        return True
    except Exception:
        logger.exception("打开浏览器失败")
        return False


def shutdown_server() -> None:
    """应用退出时调用。"""
    global _SERVER
    with _LOCK:
        if _SERVER is not None:
            _SERVER.shutdown()
            _SERVER = None
