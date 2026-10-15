"""设置页分组标题，复用同类标签的统一样式。"""
from PySide6.QtWidgets import QLabel
from beehive.common.ui.styles.labels import section_label_qss


def section_label(text, colors):
    label = QLabel(text)
    label.setStyleSheet(section_label_qss(colors))
    return label
