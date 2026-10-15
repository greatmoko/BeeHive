"""标题与提示文字样式。"""
from PySide6.QtWidgets import QLabel, QFrame


def hint_label_qss(colors: dict) -> str:
    return (
        f"font-size: 11px; color: {colors.get('text_hint', '#b0b0b0')};"
        " background: transparent; border: none;"
    )


def title_label_qss(colors: dict, *, size: int = 20) -> str:
    return (
        f"font-size: {size}px; font-weight: bold; color: {colors.get('text', '#1a1a1a')};"
        " background: transparent; border: none;"
    )


def section_label_qss(colors: dict) -> str:
    return (
        f"font-size: 14px; font-weight: bold; color: {colors.get('text', '#1a1a1a')};"
        " background: transparent; border: none;"
    )


def style_hint_label(lbl: QLabel, colors: dict):
    lbl.setWordWrap(True)
    lbl.setFrameShape(QFrame.Shape.NoFrame)
    lbl.setStyleSheet(hint_label_qss(colors))
