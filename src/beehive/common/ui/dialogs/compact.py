"""紧凑提示与操作结果表面；只负责显示，不执行数据操作。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from beehive.common.ui.theme import Theme


def show_compact_tip(parent: QWidget, theme: "Theme", message: str):
    """统一的提示弹窗，替代 QMessageBox。"""
    c = theme.colors
    dlg = QDialog(parent)
    dlg.setWindowTitle("提示")
    dlg.setFixedSize(340, 140)
    dlg.setStyleSheet(f"background: {c['content_bg']};")

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 20, 24, 18)
    layout.setSpacing(16)

    msg = QLabel(message)
    msg.setWordWrap(True)
    msg.setStyleSheet(f"font-size: 14px; color: {c['text']};")
    layout.addWidget(msg)

    layout.addStretch()

    btn_row = QHBoxLayout()
    btn_row.addStretch()
    ok_btn = QPushButton("知道了")
    ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    ok_btn.setFixedSize(80, 34)
    ok_btn.setStyleSheet(f"""
        QPushButton {{
            background: {c["btn_bg"]}; color: {c["text"]};
            border: none; border-radius: 6px; font-size: 13px;
        }}
        QPushButton:hover {{ background: {c["btn_hover"]}; }}
    """)
    ok_btn.clicked.connect(dlg.accept)
    btn_row.addWidget(ok_btn)
    layout.addLayout(btn_row)

    dlg.exec()


def show_cleanup_result(parent, theme, text):
    c = theme.colors
    dlg = QDialog(parent)
    dlg.setWindowTitle("提示")
    dlg.setFixedSize(300, 120)
    dlg.setStyleSheet(f"background: {c['content_bg']};")

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 20, 24, 16)
    layout.setSpacing(12)

    msg = QLabel(text)
    msg.setStyleSheet(f"font-size: 13px; color: {c['text']};")
    layout.addWidget(msg)

    layout.addStretch()

    btn_row = QHBoxLayout()
    btn_row.addStretch()
    ok_btn = QPushButton("知道了")
    ok_btn.setFixedSize(80, 32)
    ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    ok_btn.setStyleSheet(f"""
        QPushButton {{
            background: {c["btn_primary"]};
            color: #ffffff;
            border: none;
            border-radius: 6px;
            font-size: 13px;
        }}
    """)
    ok_btn.clicked.connect(dlg.accept)
    btn_row.addWidget(ok_btn)
    layout.addLayout(btn_row)

    dlg.exec()
