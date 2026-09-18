"""不依赖业务模块的通用 Qt 对话框。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


def show_tip(parent: QWidget, theme, message: str, *, height: int = 140) -> None:
    """显示项目统一样式的单按钮提示框。"""
    colors = theme.colors
    dialog = QDialog(parent)
    dialog.setWindowTitle("提示")
    dialog.setFixedSize(360, height)
    dialog.setStyleSheet(f"background: {colors['content_bg']};")
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 20, 24, 18)
    label = QLabel(message)
    label.setWordWrap(True)
    label.setStyleSheet(f"font-size: 14px; color: {colors['text']};")
    layout.addWidget(label)
    layout.addStretch()
    row = QHBoxLayout()
    row.addStretch()
    ok = QPushButton("知道了")
    ok.setFixedSize(80, 34)
    ok.setCursor(Qt.CursorShape.PointingHandCursor)
    ok.setStyleSheet(
        f"QPushButton {{ background: {colors['btn_bg']}; color: {colors['text']}; border: none; border-radius: 6px; font-size: 13px; }}"
        f"QPushButton:hover {{ background: {colors['btn_hover']}; }}"
    )
    ok.clicked.connect(dialog.accept)
    row.addWidget(ok)
    layout.addLayout(row)
    dialog.exec()
