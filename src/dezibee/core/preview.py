"""DeziBee Preview Server：静态文件服务 + 系统默认浏览器打开。

按需求文档第二节「不要在 WokBee 内嵌浏览器」「调用系统默认浏览器预览」：
 - 用 Python 标准库 http.server（ThreadingHTTPServer + SimpleHTTPRequestHandler）
   静态目录服务，零新增依赖；
 - 绑定 127.0.0.1（仅本机），滚动可用端口（默认 38681 起）；
 - 每个需求 URL：http://127.0.0.1:<port>/<需求ID>/
 - 打开浏览器复用项目现有能力：dialogs.open_path / webbrowser.open。
"""

from __future__ import annotations

import logging
import socket
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logger = logging.getLogger("dezibee")

_DEFAULT_PORT = 38681
_SERVER: _PreviewServer | None = None
_LOCK = threading.Lock()


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
    """安静版静态处理器：不往 stdout 打日志。"""

    def log_message(self, format, *args):  # noqa: A002
        return


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
    自动重定向到它（预览直达 Demo 首页）；否则正常静态文件/目录列出。
    """

    class _RootedHandler(_Handler):
        def translate_path(self, path: str) -> str:
            import posixpath
            import urllib.parse

            path = urllib.parse.unquote(path)
            path = path.split("?", 1)[0]
            norm = posixpath.normpath(path).lstrip("/")
            parts = [p for p in norm.split("/") if p not in ("", ".", "..")] if norm else []
            target = root.joinpath(*parts) if parts else root
            # 需求目录根（一层的目录）：指向 demo/index.html
            if len(parts) == 1 and target.is_dir():
                alt = target / "demo" / "index.html"
                if alt.exists():
                    return str(alt.resolve())
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
