"""日志工具。"""

import faulthandler
import logging
import os
import sys
import threading
from pathlib import Path
from tokbee.core.config import default_data_dir

from tokbee import __app_name__

APP_LOGGER_NAMES = (__app_name__, "wokbee", "tokbee", "dezibee", "autobee")
_app_handlers: tuple[logging.Handler, ...] | None = None
_fault_log = None
_crash_reporting_enabled = False


def _open_log_file(log_dir: Path, filename: str) -> tuple[object, Path]:
    """优先共用日志；被旧进程锁定时退回当前进程独立日志。"""
    path = log_dir / filename
    try:
        return path.open("a", encoding="utf-8", buffering=1), path
    except OSError:
        fallback = log_dir / f"{Path(filename).stem}_{os.getpid()}.log"
        return fallback.open("a", encoding="utf-8", buffering=1), fallback


def setup_logger(level: int = logging.INFO) -> logging.Logger:
    """初始化全部应用命名空间的控制台与文件日志。"""
    global _app_handlers
    logger = logging.getLogger(__app_name__)
    logger.setLevel(level)

    if _app_handlers is None:
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(level)
        fmt = logging.Formatter(
            "[%(asctime)s] %(levelname)-8s %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        console.setFormatter(fmt)
        log_dir = default_data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_stream, _ = _open_log_file(log_dir, "tokbee.log")
        file_handler = logging.StreamHandler(log_stream)
        file_handler.setLevel(level)
        file_handler.setFormatter(fmt)
        _app_handlers = (console, file_handler)
    for name in APP_LOGGER_NAMES:
        app_logger = logging.getLogger(name)
        app_logger.setLevel(level)
        for handler in _app_handlers:
            if handler not in app_logger.handlers:
                app_logger.addHandler(handler)
        # 这些 logger 不是同一命名空间的父子关系；关闭传播避免 root 配置后重复输出。
        app_logger.propagate = False

    return logger


def setup_crash_reporting(logger: logging.Logger) -> Path | None:
    """将未捕获异常和原生崩溃栈写入可定位的日志文件。"""
    global _fault_log, _crash_reporting_enabled
    if _crash_reporting_enabled:
        return None
    _crash_reporting_enabled = True
    try:
        log_dir = default_data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        _fault_log, path = _open_log_file(log_dir, "fatal.log")
        _fault_log.write("\n=== WokBee crash diagnostics enabled ===\n")
        faulthandler.enable(_fault_log, all_threads=True)
    except OSError:
        logger.exception("无法启用 fatal.log 崩溃诊断")
        path = None

    def report_main_exception(exc_type, exc_value, traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            return
        logger.critical(
            "未捕获的主线程异常", exc_info=(exc_type, exc_value, traceback)
        )

    def report_thread_exception(args) -> None:
        if issubclass(args.exc_type, SystemExit):
            return
        logger.critical(
            "后台线程 %s 未捕获异常",
            getattr(args.thread, "name", "unknown"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = report_main_exception
    threading.excepthook = report_thread_exception
    return path
