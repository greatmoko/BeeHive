"""原子文件写入工具 — 写临时文件 + rename，防止中断导致数据损坏。"""

import json
import os
import tempfile
import threading
from pathlib import Path

from tokbee.core.errors import StorageError


# Windows 下多个后台线程同时进行 tempfile + os.replace 时，文件系统过滤器/杀毒软件
# 可能在替换瞬间触发原生访问冲突。所有进程内原子写入共用一把锁，保持替换操作串行。
_WRITE_LOCK = threading.RLock()


def safe_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """原子写入文本文件：先写临时文件，再 rename 替换目标。"""
    with _WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd, tmp = tempfile.mkstemp(
                dir=str(path.parent), suffix=".tmp", prefix=f".{path.stem}_"
            )
            try:
                with os.fdopen(fd, "w", encoding=encoding) as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, str(path))
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            raise StorageError(f"写入文件失败: {path} — {e}") from e


def safe_write_json(path: Path, data, *, indent: int = 2) -> None:
    """原子写入 JSON 文件。"""
    content = json.dumps(data, indent=indent, ensure_ascii=False)
    safe_write_text(path, content)
