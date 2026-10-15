"""附件状态与文件保存，由业务明确传入目标目录。"""
from __future__ import annotations
import re
import shutil
import time
from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}

def is_image_file(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def sanitize_attachment_name(name: str) -> str:
    """移除路径与 Windows 非法字符，返回安全文件名。"""
    cleaned = re.sub(r"[\\\\/:*?\"<>|]+", "_", (name or "attachment").strip())
    return cleaned or "attachment"


def copy_attachment_file(source: Path, target: Path) -> Path:
    """复制已有文件到明确给定的目标位置。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def persist_inline_attachment(item: dict, root: Path | None) -> Path | str | None:
    """将附件保存至 ``root``；保存失败时保持原有路径。"""
    source = item.get("path")
    if root is None:
        return source
    name = sanitize_attachment_name(str(item.get("display_name") or "attachment"))
    try:
        root.mkdir(parents=True, exist_ok=True)
        target = root / name
        if target.exists():
            target = root / (
                f"{target.stem}_{time.strftime('%Y%m%d_%H%M%S')}"
                f"_{time.time_ns() % 1_000_000:06d}{target.suffix}"
            )
        if source is not None and Path(str(source)).exists():
            copy_attachment_file(Path(str(source)), target)
        elif item.get("data") is not None:
            target.write_bytes(item["data"])
        else:
            return source
        item["path"] = target
        item["display_name"] = target.name
        return target
    except OSError:
        return source


class AttachmentState:
    """附件列表及其可选的上传目录；页面只负责触发刷新。"""

    def __init__(self) -> None:
        self.items: list[dict] = []
        self.uploads_root: Path | None = None

    def set_uploads_root(self, root: str | Path | None) -> None:
        self.uploads_root = Path(root) if root else None

    def add(self, item: dict) -> bool:
        source = item.get("path")
        if source and any(existing.get("path") == source for existing in self.items):
            return False
        self.persist(item)
        self.items.append(item)
        return True

    def remove(self, item: dict) -> None:
        for index, existing in enumerate(self.items):
            if existing is item:
                self.items.pop(index)
                return

    def persist(self, item: dict) -> Path | str | None:
        return persist_inline_attachment(item, self.uploads_root)

    def take(self) -> list[dict]:
        items = list(self.items)
        for item in items:
            self.persist(item)
        self.items.clear()
        return items
