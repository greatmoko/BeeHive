"""预览静态路由、PRD 保存与单文件导出。"""
from __future__ import annotations
import json
import logging
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from beehive.features.dezibee.core.workbench_prd import _BadRequest, _resolve_demo_index, _rewrite_prd_in_index
logger = logging.getLogger("dezibee")
_PRD_ENDPOINT = "/__dezibee__/prd"

_EXPORT_ENDPOINT = "/__dezibee__/export"

_MAX_BODY = 20 * 1024 * 1024  # 20MB 上限

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

            from beehive.features.dezibee.core.packager import build_single_file

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
                if not isinstance(payload, dict):
                    raise _BadRequest("JSON 请求体必须是对象")
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
