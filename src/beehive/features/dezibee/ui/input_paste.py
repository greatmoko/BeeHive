"""需求输入粘贴识别，附件状态仍由输入条持有。"""
from __future__ import annotations
import time
from pathlib import Path
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage
from beehive.common.io.attachments import is_image_file

def handle_paste(bar) -> bool:
    """Ctrl+V / 剪贴板事件：识别图片与文件附件（与 WokBee 同逻辑）。"""
    from PySide6.QtWidgets import QApplication

    cb = QApplication.clipboard()
    mime = cb.mimeData()
    got = False
    # 文件粘贴优先使用本地 URL，保留原始文件名（包括复制的图片文件）
    if mime.hasUrls():
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            if not p.exists() or not p.is_file():
                continue
            bar._add_attachment(
                {
                    "kind": "image" if is_image_file(p) else "file",
                    "display_name": p.name,
                    "data": None,
                    "path": p,
                }
            )
        got = True
    # 纯截图/剪贴板图片没有原始文件名，只能生成唯一名称
    elif mime.hasImage():
        image = cb.image()
        if not image.isNull():
            img = image if isinstance(image, QImage) else image.toImage()
            try:
                buf = QBuffer()
                buf.open(QIODevice.WriteOnly)
                suffix = "png"
                if not img.save(buf, "PNG"):
                    img.save(buf, "JPG")
                    suffix = "jpg"
                data = bytes(buf.data())
                bar._add_attachment(
                    {
                        "kind": "image",
                        "display_name": (
                            f"pasted_image_{time.strftime('%Y%m%d_%H%M%S')}"
                            f"_{time.time_ns() % 1_000_000:06d}.png"
                            if suffix == "png"
                            else f"pasted_image_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
                        ),
                        "data": data,
                        "path": None,
                    }
                )
            except Exception:
                pass
            got = True
    return got
