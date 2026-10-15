"""下拉框样式与选项绘制。"""
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionViewItem, QComboBox, QFrame, QStyleFactory
from beehive.common.ui.styles.icons import _arrow_url
from beehive.common.ui.styles.metrics import RADIUS, DEFAULT_COMBO_WIDTH, DEFAULT_COMBO_HEIGHT


class _NoFocusDelegate(QStyledItemDelegate):
    """下拉选项自绘，去掉焦点黑框。"""

    def __init__(self, parent=None, *, hover_color: str = "#e5e5e5", text_color: str = "#1a1a1a"):
        super().__init__(parent)
        self._hover = QColor(hover_color)
        self._text = QColor(text_color)

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        rect = opt.rect
        hovered = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        if hovered or selected:
            painter.fillRect(rect, self._hover)
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if text is None:
            text = ""
        painter.save()
        painter.setPen(self._text)
        painter.drawText(
            rect.adjusted(10, 0, -10, 0),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            str(text),
        )
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        s = super().sizeHint(option, index)
        return QSize(s.width(), max(s.height(), 28))


def underline_combo_qss(colors: dict) -> str:
    """扁平仅下边框下拉（特殊场景；表单默认用 rounded）。"""
    text = colors.get("text", "#1a1a1a")
    hint = colors.get("text_hint", "#b0b0b0")
    border = colors.get("input_border", "#e0e0e0")
    focus = colors.get("input_focus_border", colors.get("accent", "#07c160"))
    arrow = _arrow_url()
    return f"""
        QComboBox {{
            background: transparent; color: {text};
            border: none; border-bottom: 1px solid {border}; border-radius: 0;
            padding: 2px 28px 2px 4px; font-size: 13px; min-height: 34px;
        }}
        QComboBox:hover {{ border-bottom: 1px solid {focus}; }}
        QComboBox:focus {{ border-bottom: 1px solid {focus}; }}
        QComboBox:disabled {{ color: {hint}; }}
        QComboBox::drop-down {{ border: none; width: 28px; background: transparent; }}
        QComboBox::down-arrow {{ image: url("{arrow}"); width: 12px; height: 8px; }}
    """


def rounded_combo_qss(colors: dict) -> str:
    """默认圆角下拉：高 34，实心三角。"""
    text = colors.get("text", "#1a1a1a")
    hint = colors.get("text_hint", "#b0b0b0")
    bg = colors.get("input_bg", "#f5f5f5")
    border = colors.get("input_border", "#e0e0e0")
    focus = colors.get("input_focus_border", colors.get("accent", "#07c160"))
    arrow = _arrow_url()
    return f"""
        QComboBox {{
            background: {bg}; color: {text};
            border: 1px solid {border}; border-radius: {RADIUS}px;
            padding: 0 28px 0 12px; font-size: 13px;
            min-height: {DEFAULT_COMBO_HEIGHT}px; max-height: {DEFAULT_COMBO_HEIGHT}px;
        }}
        QComboBox:hover {{ border: 1px solid {focus}; }}
        QComboBox:focus {{ border: 1px solid {focus}; }}
        QComboBox:disabled {{ color: {hint}; }}
        QComboBox::drop-down {{
            subcontrol-origin: padding; subcontrol-position: center right;
            border: none; width: 28px; background: transparent;
        }}
        QComboBox::down-arrow {{ image: url("{arrow}"); width: 12px; height: 8px; }}
    """


def apply_combo_popup_style(
    combo: QComboBox,
    colors: dict,
    *,
    underline: bool = False,
    rounded: bool = False,
    fixed_width: int | None = None,
    fixed_height: int | None = None,
):
    """应用下拉框样式与弹出列表样式。

    表单默认请用 ``apply_form_combo``（rounded + 300×34）。
    """
    hover = colors.get("btn_hover", "#e5e5e5")
    bg = colors.get("content_bg", "#ffffff")
    text = colors.get("text", "#1a1a1a")
    border = colors.get("input_border", "#e0e0e0")

    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        combo.setStyle(fusion)

    if underline:
        combo.setStyleSheet(underline_combo_qss(colors))
    elif rounded:
        combo.setStyleSheet(rounded_combo_qss(colors))

    if fixed_width is not None and fixed_height is not None:
        combo.setFixedSize(fixed_width, fixed_height)
    elif fixed_width is not None:
        combo.setFixedWidth(fixed_width)
    elif fixed_height is not None:
        combo.setFixedHeight(fixed_height)
    elif rounded:
        combo.setFixedHeight(DEFAULT_COMBO_HEIGHT)

    view = combo.view()
    view.setItemDelegate(_NoFocusDelegate(view, hover_color=hover, text_color=text))
    view.setFrameShape(QFrame.Shape.NoFrame)
    view.setMouseTracking(True)
    view.setStyleSheet(f"""
        QAbstractItemView, QListView {{
            background: {bg}; border: 1px solid {border}; outline: 0px;
        }}
        QAbstractItemView::item, QListView::item {{
            border: none; outline: 0px;
        }}
    """)


def apply_form_combo(combo: QComboBox, colors: dict, *, width: int = DEFAULT_COMBO_WIDTH):
    """表单默认下拉：圆角 + 固定宽高 + 实心三角。"""
    apply_combo_popup_style(
        combo, colors, rounded=True,
        fixed_width=width, fixed_height=DEFAULT_COMBO_HEIGHT,
    )
