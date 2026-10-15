"""复选框与单选框样式。"""
from PySide6.QtWidgets import QCheckBox
from beehive.common.ui.styles.icons import _check_url
from beehive.common.ui.styles.metrics import RADIUS, DEFAULT_COMBO_WIDTH, DEFAULT_COMBO_HEIGHT, DEFAULT_INPUT_HEIGHT, DEFAULT_BTN_HEIGHT


def checkbox_qss(colors: dict) -> str:
    """勾选框：绿色选中 + 白勾。"""
    text = colors.get("text", "#1a1a1a")
    accent = colors.get("accent", "#07c160")
    border = colors.get("input_border", "#e0e0e0")
    bg = colors.get("input_bg", "#f5f5f5")
    check = _check_url()
    return f"""
        QCheckBox {{
            color: {text}; font-size: 13px;
            background: transparent; border: none; spacing: 6px;
        }}
        QCheckBox:focus {{ outline: none; border: none; }}
        QCheckBox::indicator {{
            width: 16px; height: 16px;
            border: 1px solid {border}; border-radius: 3px; background: {bg};
        }}
        QCheckBox::indicator:hover {{ border-color: {accent}; }}
        QCheckBox::indicator:checked {{
            background: {accent}; border-color: {accent};
            image: url("{check}");
        }}
        QCheckBox::indicator:disabled {{
            background: {border}; border-color: {border};
        }}
    """


def apply_checkbox(chk: QCheckBox, colors: dict):
    chk.setStyleSheet(checkbox_qss(colors))


def radiobutton_qss(colors: dict) -> str:
    """单选框：右侧圆点，选中为实心 accent 色。"""
    text = colors.get("text", "#1a1a1a")
    accent = colors.get("accent", "#07c160")
    border = colors.get("input_border", "#e0e0e0")
    bg = colors.get("input_bg", "#f5f5f5")
    return f"""
        QRadioButton {{
            color: {text}; font-size: 13px;
            background: transparent; border: none; spacing: 6px;
        }}
        QRadioButton:focus {{ outline: none; border: none; }}
        QRadioButton::indicator {{
            width: 16px; height: 16px;
            border: 1px solid {border}; border-radius: 8px; background: {bg};
        }}
        QRadioButton::indicator:hover {{ border-color: {accent}; }}
        QRadioButton::indicator:checked {{
            background: {accent}; border: 1px solid {accent};
        }}
        QRadioButton::indicator:disabled {{
            background: {border}; border-color: {border};
        }}
    """


def apply_radio(chk, colors: dict):
    chk.setStyleSheet(radiobutton_qss(colors))
