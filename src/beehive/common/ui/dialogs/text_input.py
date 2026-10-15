"""通用单行与多行文本输入。"""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextEdit, QVBoxLayout, QWidget
from beehive.common.ui.theme import Theme
from beehive.common.ui.styles.menus import bind_text_edit_context_menu


def _ask_text(
    parent: QWidget,
    theme: Theme,
    title: str,
    label: str,
    default: str = "",
    *,
    max_length: int | None = None,
) -> str | None:
    c = theme.colors
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setFixedSize(400, 180)
    dlg.setStyleSheet(f"background: {c['content_bg']};")
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 20, 24, 18)
    layout.setSpacing(12)
    layout.addWidget(QLabel(label))
    inp = QLineEdit(default)
    inp.setFixedHeight(34)
    if max_length is not None and max_length > 0:
        inp.setMaxLength(max_length)
    inp.setStyleSheet(f"""
        QLineEdit {{
            background: {c["input_bg"]}; color: {c["text"]};
            border: 1px solid {c["input_border"]}; border-radius: 6px; padding: 0 10px;
        }}
    """)
    layout.addWidget(inp)
    layout.addStretch()
    row = QHBoxLayout()
    row.addStretch()
    cancel = QPushButton("取消")
    cancel.setFixedSize(72, 34)
    cancel.setStyleSheet(f"""
        QPushButton {{
            background: {c["btn_bg"]}; color: {c["text"]};
            border: none; border-radius: 6px;
        }}
        QPushButton:hover {{ background: {c["btn_hover"]}; }}
    """)
    cancel.clicked.connect(dlg.reject)
    ok = QPushButton("确定")
    ok.setFixedSize(72, 34)
    ok.setStyleSheet(f"""
        QPushButton {{
            background: {c["btn_primary"]}; color: white;
            border: none; border-radius: 6px;
        }}
        QPushButton:hover {{ background: {c["btn_primary_hover"]}; }}
    """)
    ok.clicked.connect(dlg.accept)
    row.addWidget(cancel)
    row.addWidget(ok)
    layout.addLayout(row)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return inp.text().strip()
    return None


def _textedit_qss(theme: Theme) -> str:
    c = theme.colors
    return f"""
        QTextEdit {{
            background: {c["input_bg"]}; color: {c["text"]};
            border: 1px solid {c["input_border"]}; border-radius: 6px;
            padding: 8px; font-size: 13px;
        }}
        QTextEdit:focus {{ border: 1px solid {c["input_focus_border"]}; }}
    """


def _ask_multiline(
    parent: QWidget,
    theme: Theme,
    title: str,
    label: str,
    default: str = "",
    *,
    min_lines: int = 5,
) -> str | None:
    """多行文本输入（至少约 min_lines 行，超出滚动）。"""
    c = theme.colors
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setMinimumSize(480, 320)
    dlg.resize(520, 360)
    dlg.setStyleSheet(f"background: {c['content_bg']};")
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 20, 24, 18)
    layout.setSpacing(10)
    tip = QLabel(label)
    tip.setWordWrap(True)
    tip.setStyleSheet(f"font-size: 13px; color: {c['text']};")
    layout.addWidget(tip)
    inp = QTextEdit()
    inp.setPlainText(default or "")
    bind_text_edit_context_menu(inp, c)
    # ~22px/行 + padding
    inp.setMinimumHeight(max(5, min_lines) * 22 + 16)
    inp.setStyleSheet(_textedit_qss(theme))
    layout.addWidget(inp, stretch=1)
    row = QHBoxLayout()
    row.addStretch()
    cancel = QPushButton("取消")
    cancel.setFixedSize(72, 34)
    cancel.setStyleSheet(f"""
        QPushButton {{
            background: {c["btn_bg"]}; color: {c["text"]};
            border: none; border-radius: 6px;
        }}
        QPushButton:hover {{ background: {c["btn_hover"]}; }}
    """)
    cancel.clicked.connect(dlg.reject)
    ok = QPushButton("确定")
    ok.setFixedSize(72, 34)
    ok.setStyleSheet(f"""
        QPushButton {{
            background: {c["btn_primary"]}; color: white;
            border: none; border-radius: 6px;
        }}
        QPushButton:hover {{ background: {c["btn_primary_hover"]}; }}
    """)
    ok.clicked.connect(dlg.accept)
    row.addWidget(cancel)
    row.addWidget(ok)
    layout.addLayout(row)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return inp.toPlainText().strip()
    return None
