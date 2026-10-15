"""按钮样式与交互状态。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton
from beehive.common.ui.styles.metrics import RADIUS, DEFAULT_COMBO_WIDTH, DEFAULT_COMBO_HEIGHT, DEFAULT_INPUT_HEIGHT, DEFAULT_BTN_HEIGHT


def primary_btn_qss(colors: dict) -> str:
    """主按钮：绿底白字。"""
    return f"""
        QPushButton {{
            background: {colors.get("btn_primary", "#07c160")}; color: #ffffff;
            border: none; border-radius: {RADIUS}px;
            padding: 0 14px; font-size: 13px; text-decoration: none; outline: none;
        }}
        QPushButton:hover {{ background: {colors.get("btn_primary_hover", "#06ad56")}; }}
        QPushButton:disabled {{
            background: {colors.get("btn_bg", "#f0f0f0")};
            color: {colors.get("text_hint", "#b0b0b0")};
        }}
        QPushButton:focus {{ outline: none; }}
    """


def secondary_btn_qss(colors: dict) -> str:
    """次要按钮：灰底黑字。"""
    text = colors.get("text", "#1a1a1a")
    bg = colors.get("btn_bg", "#f0f0f0")
    hover = colors.get("btn_hover", "#e5e5e5")
    return f"""
        QPushButton {{
            background: {bg}; color: {text};
            border: none; border-radius: {RADIUS}px;
            padding: 0 14px; font-size: 13px; text-decoration: none; outline: none;
        }}
        QPushButton:hover {{ background: {hover}; }}
        QPushButton:focus {{ outline: none; border: none; }}
        QPushButton:pressed {{ background: {hover}; }}
        QPushButton:disabled {{ color: {colors.get("text_hint", "#b0b0b0")}; }}
    """


def danger_btn_qss(colors: dict) -> str:
    """危险操作：灰底红字。"""
    return f"""
        QPushButton {{
            background: {colors.get("btn_bg", "#f0f0f0")};
            color: {colors.get("danger", "#f56c6c")};
            border: none; border-radius: {RADIUS}px;
            padding: 0 14px; font-size: 13px; text-decoration: none; outline: none;
        }}
        QPushButton:hover {{ background: {colors.get("btn_hover", "#e5e5e5")}; }}
        QPushButton:disabled {{ color: {colors.get("text_hint", "#b0b0b0")}; }}
        QPushButton:focus {{ outline: none; }}
    """


def apply_primary_btn(btn: QPushButton, colors: dict, *, height: int = DEFAULT_BTN_HEIGHT):
    btn.setFixedHeight(height)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(primary_btn_qss(colors))


def apply_secondary_btn(btn: QPushButton, colors: dict, *, height: int = DEFAULT_BTN_HEIGHT):
    btn.setFixedHeight(height)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setAutoDefault(False)
    btn.setDefault(False)
    btn.setStyleSheet(secondary_btn_qss(colors))


def apply_danger_btn(btn: QPushButton, colors: dict, *, height: int = DEFAULT_BTN_HEIGHT):
    btn.setFixedHeight(height)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setAutoDefault(False)
    btn.setDefault(False)
    btn.setStyleSheet(danger_btn_qss(colors))


def icon_btn_qss(colors: dict) -> str:
    """图标按钮：无文字、无边框，悬停变灰、按下浅绿。"""
    c = colors
    return f"""
        QPushButton {{
            background: {c["btn_bg"]}; color: {c["text"]};
            border: none; border-radius: {RADIUS}px; font-size: 16px;
            padding: 0;
        }}
        QPushButton:hover {{ background: {c["btn_hover"]}; }}
        QPushButton:pressed {{ background: {c["accent_light"]}; }}
    """


def apply_icon_btn(btn: QPushButton, colors: dict, *, size: int | None = None):
    """图标按钮：无文字、无边框，可选方形尺寸。"""
    if size is not None:
        btn.setFixedSize(size, size)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(icon_btn_qss(colors))


def destructive_btn_qss(colors: dict) -> str:
    c = colors
    return f"""
        QPushButton {{
            background: {c["danger"]};
            color: #ffffff;
            border: none;
            border-radius: 6px;
            padding: 0 16px;
            font-size: 13px;
        }}
        QPushButton:hover {{
            background: {c.get("danger_hover", "#c0392b")};
        }}
    """
