"""无业务依赖的附件状态、落盘与 chip 展示。"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from PySide6.QtCore import QFileInfo, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import QFileIconProvider, QFrame, QHBoxLayout, QLabel, QPushButton, QBoxLayout, QWidget


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


def open_attachment(item: dict) -> None:
    """使用系统默认程序打开可预览附件。"""
    path = item.get("path")
    if path:
        path = Path(str(path))
    else:
        data = item.get("data")
        if not data:
            return
        suffix = Path(sanitize_attachment_name(str(item.get("display_name") or "attachment"))).suffix.lower()
        suffix = suffix or (".png" if item.get("kind") == "image" else ".txt")
        path = Path(tempfile.gettempdir()) / f"attachment_{time.time_ns()}{suffix}"
        try:
            path.write_bytes(data)
        except OSError:
            return

    if path.suffix.lower() not in IMAGE_EXTENSIONS | {".pdf", ".txt", ".md"} or not path.exists():
        return
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class AttachmentChip(QFrame):
    """输入框上方的附件 chip；主题由宿主应用传入。"""

    remove_clicked = Signal(object)

    def __init__(self, item: dict, theme: Any, parent=None):
        super().__init__(parent)
        self.item = item
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击打开附件")
        colors = theme.colors
        self.setStyleSheet(f"""
            AttachmentChip {{
                background: {colors['input_bg']};
                border: 1px solid {colors['input_border']};
                border-radius: 8px;
            }}
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)
        name = item.get("display_name") or "附件"

        if item.get("kind") == "image":
            thumbnail = QLabel()
            pixmap = QPixmap()
            pixmap.loadFromData(item.get("data") or b"")
            if pixmap.isNull():
                thumbnail.setText("🖼")
            else:
                thumbnail.setPixmap(pixmap.scaled(26, 26, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                thumbnail.setScaledContents(False)
            thumbnail.setFixedSize(26, 26)
            thumbnail.setStyleSheet("background: transparent; border: none;")
            thumbnail.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(thumbnail)
            visible_name = name if len(name) <= 18 else name[:8] + "…" + name[-6:]
        else:
            try:
                icon = QFileIconProvider().icon(QFileInfo(str(item.get("path") or "")))
            except Exception:
                icon = QFileIconProvider().icon(QFileIconProvider.IconType.File)
            icon_label = QLabel()
            icon_label.setPixmap(icon.pixmap(18, 18))
            icon_label.setFixedSize(18, 18)
            icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(icon_label)
            visible_name = name if len(name) <= 24 else name[:12] + "…" + name[-8:]

        name_label = QLabel(visible_name)
        name_label.setToolTip(name)
        name_label.setStyleSheet(f"font-size: 12px; color: {colors['text']}; background: transparent; border: none;")
        name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(name_label)
        remove_button = QPushButton("×")
        remove_button.setFixedSize(18, 18)
        remove_button.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_button.setToolTip("移除附件")
        remove_button.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {colors['text_hint']}; border: none; border-radius: 9px; font-size: 13px; font-weight: bold; }}
            QPushButton:hover {{ background: {colors['btn_hover']}; color: {colors['danger']}; }}
        """)
        remove_button.clicked.connect(lambda: self.remove_clicked.emit(self.item))
        layout.addWidget(remove_button)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            open_attachment(self.item)
        super().mousePressEvent(event)


def refresh_attachment_bar(
    layout: QBoxLayout,
    bar: QWidget,
    items: Sequence[dict],
    theme: Any,
    remove_item: Callable[[dict], None],
) -> None:
    """用当前附件列表刷新 chip 条；布局最后一项必须是 stretch。"""
    while layout.count() > 1:
        widget = layout.takeAt(0).widget()
        if widget is not None:
            widget.setParent(None)
    for item in items:
        chip = AttachmentChip(item, theme)
        chip.remove_clicked.connect(remove_item)
        layout.insertWidget(layout.count() - 1, chip)
    bar.setVisible(bool(items))
    bar.adjustSize()
