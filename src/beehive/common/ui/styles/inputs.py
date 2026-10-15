"""文本与数值输入样式。"""
from PySide6.QtWidgets import QLineEdit, QTextEdit, QAbstractSpinBox
from beehive.common.ui.styles.metrics import RADIUS, DEFAULT_COMBO_WIDTH, DEFAULT_COMBO_HEIGHT, DEFAULT_INPUT_HEIGHT, DEFAULT_BTN_HEIGHT


def rounded_lineedit_qss(colors: dict) -> str:
    text = colors.get("text", "#1a1a1a")
    bg = colors.get("input_bg", "#f5f5f5")
    border = colors.get("input_border", "#e0e0e0")
    focus = colors.get("input_focus_border", colors.get("accent", "#07c160"))
    return f"""
        QLineEdit {{
            background: {bg}; color: {text};
            border: 1px solid {border}; border-radius: {RADIUS}px;
            padding: 0 10px; font-size: 13px; min-height: {DEFAULT_INPUT_HEIGHT}px;
        }}
        QLineEdit:focus {{ border: 1px solid {focus}; }}
    """


def rounded_textedit_qss(colors: dict) -> str:
    text = colors.get("text", "#1a1a1a")
    bg = colors.get("input_bg", "#f5f5f5")
    border = colors.get("input_border", "#e0e0e0")
    focus = colors.get("input_focus_border", colors.get("accent", "#07c160"))
    return f"""
        QTextEdit {{
            background: {bg}; color: {text};
            border: 1px solid {border}; border-radius: 8px;
            padding: 8px; font-size: 13px;
        }}
        QTextEdit:focus {{ border: 1px solid {focus}; }}
    """


def rounded_spin_qss(colors: dict) -> str:
    text = colors.get("text", "#1a1a1a")
    hint = colors.get("text_hint", "#b0b0b0")
    bg = colors.get("input_bg", "#f5f5f5")
    border = colors.get("input_border", "#e0e0e0")
    focus = colors.get("input_focus_border", colors.get("accent", "#07c160"))
    return f"""
        QSpinBox, QDoubleSpinBox {{
            background: {bg}; color: {text};
            border: 1px solid {border}; border-radius: {RADIUS}px;
            padding: 2px 6px; font-size: 13px; min-height: 32px;
        }}
        QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {focus}; }}
        QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {hint}; }}
        QSpinBox::up-button, QSpinBox::down-button,
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
            width: 18px; border: none; background: transparent;
        }}
    """


def apply_lineedit(edit: QLineEdit, colors: dict, *, height: int = DEFAULT_INPUT_HEIGHT):
    edit.setFixedHeight(height)
    edit.setStyleSheet(rounded_lineedit_qss(colors))


def apply_textedit(edit: QTextEdit, colors: dict):
    edit.setStyleSheet(rounded_textedit_qss(colors))


def apply_spin(spin: QAbstractSpinBox, colors: dict):
    spin.setStyleSheet(rounded_spin_qss(colors))
