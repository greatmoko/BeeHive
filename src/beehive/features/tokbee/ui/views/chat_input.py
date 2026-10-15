"""粘贴与拖入输入交互；文件识别与图片落盘由业务传入。"""
import logging
from PySide6.QtCore import Signal, QMimeData
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QTextEdit
from beehive.common.ui.theme import COLORS
from beehive.common.ui.styles.menus import exec_text_edit_context_menu

logger = logging.getLogger("tokbee")


class _ChatInputEdit(QTextEdit):
    """支持粘贴/拖入图片与文件的输入框。"""

    files_dropped = Signal(list)  # list[str] 本地路径

    def __init__(self, accept_file, save_image, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._accept_file = accept_file
        self._save_image = save_image
        self._menu_colors: dict = dict(COLORS)

    def set_menu_colors(self, colors: dict):
        self._menu_colors = colors

    def contextMenuEvent(self, event):
        exec_text_edit_context_menu(self, event, self._menu_colors)

    def insertFromMimeData(self, source: QMimeData):
        paths: list[str] = []

        if source.hasUrls():
            for url in source.urls():
                if url.isLocalFile():
                    fp = url.toLocalFile()
                    if self._accept_file(fp):
                        paths.append(fp)

        if not paths and source.hasImage():
            img = source.imageData()
            try:
                if isinstance(img, QPixmap) and not img.isNull():
                    paths.append(self._save_image(img))
                elif isinstance(img, QImage) and not img.isNull():
                    paths.append(self._save_image(img))
            except Exception as e:
                logger.error("保存粘贴图片失败: %s", e)

        if paths:
            self.files_dropped.emit(paths)
            # 不把 file:// 路径或文件名再插入输入框
            return

        super().insertFromMimeData(source)

    def dragEnterEvent(self, event):
        md = event.mimeData()
        if md.hasUrls() or md.hasImage():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        md = event.mimeData()
        paths: list[str] = []
        if md.hasUrls():
            for url in md.urls():
                if url.isLocalFile():
                    fp = url.toLocalFile()
                    if self._accept_file(fp):
                        paths.append(fp)
        if not paths and md.hasImage():
            img = md.imageData()
            try:
                if isinstance(img, (QImage, QPixmap)):
                    paths.append(self._save_image(img))
            except Exception as e:
                logger.error("保存拖入图片失败: %s", e)
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)
