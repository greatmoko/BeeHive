"""TokBee 待发送附件展示，不保存业务附件列表。"""
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton
from beehive.features.tokbee.core.file_reader import is_image


class ChatAttachmentBar(QFrame):
    file_removed = Signal(str)

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setStyleSheet("background: transparent;")
        self.setVisible(False)
        self._attach_bar_layout = QHBoxLayout(self)
        self._attach_bar_layout.setContentsMargins(0, 0, 0, 0)
        self._attach_bar_layout.setSpacing(6)
        self._attach_bar_layout.addStretch()

    def render(self, paths):
        layout = self._attach_bar_layout
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if not paths:
            self.setVisible(False)
            return
        self.setVisible(True)
        c = self.theme.colors
        for fp in paths:
            name = Path(fp).name
            chip = QFrame()
            chip.setStyleSheet(f"""
                QFrame {{
                    background: {c["input_bg"]};
                    border: 1px solid {c["border_light"]};
                    border-radius: 4px;
                }}
            """)
            hl = QHBoxLayout(chip)
            hl.setContentsMargins(6, 2, 2, 2)
            hl.setSpacing(4)
            if is_image(fp) and Path(fp).is_file():
                thumb = QLabel()
                pix = QPixmap(fp)
                if not pix.isNull():
                    thumb.setPixmap(pix.scaled(
                        28, 28,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    ))
                    thumb.setStyleSheet("background: transparent; border: none;")
                    hl.addWidget(thumb)
            lbl = QLabel(name)
            lbl.setStyleSheet(f"font-size: 11px; color: {c['text_secondary']}; background: transparent; border: none;")
            lbl.setToolTip(fp)
            hl.addWidget(lbl)
            x_btn = QPushButton("×")
            x_btn.setFixedSize(16, 16)
            x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            x_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {c["text_hint"]};
                    border: none; font-size: 12px; font-weight: bold;
                }}
                QPushButton:hover {{ color: {c["danger"]}; }}
            """)
            x_btn.clicked.connect(lambda _, f=fp: self.file_removed.emit(f))
            hl.addWidget(x_btn)
            layout.addWidget(chip)
        layout.addStretch()
