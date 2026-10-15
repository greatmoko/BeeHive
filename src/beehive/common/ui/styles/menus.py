"""右键菜单样式与文本编辑操作。"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QMenu, QWidget, QTextEdit


def context_menu_qss(colors: dict) -> str:
    """右键菜单 QSS：白底黑字，避免跟随系统深色主题导致看不清。"""
    bg = colors.get("content_bg", "#ffffff")
    text = colors.get("text", "#1a1a1a")
    border = colors.get("border", "#e5e5e5")
    hover = colors.get("subnav_hover", "#f0f0f0")
    hint = colors.get("text_hint", "#b0b0b0")
    return f"""
        QMenu {{
            background-color: {bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 4px;
        }}
        QMenu::item {{
            background-color: transparent;
            color: {text};
            padding: 6px 20px;
            border-radius: 4px;
            font-size: 12px;
        }}
        QMenu::item:selected {{
            background-color: {hover};
            color: {text};
        }}
        QMenu::item:disabled {{
            color: {hint};
        }}
        QMenu::separator {{
            height: 1px;
            background: {border};
            margin: 4px 8px;
        }}
    """


def apply_context_menu(menu: QMenu, colors: dict) -> None:
    """应用统一的浅色右键菜单样式（QSS + Palette 双保险）。"""
    bg = colors.get("content_bg", "#ffffff")
    text = colors.get("text", "#1a1a1a")
    hover = colors.get("subnav_hover", "#f0f0f0")
    menu.setStyleSheet(context_menu_qss(colors))
    pal = menu.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(bg))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(text))
    pal.setColor(QPalette.ColorRole.Base, QColor(bg))
    pal.setColor(QPalette.ColorRole.Text, QColor(text))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(text))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(hover))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(text))
    menu.setPalette(pal)


def make_context_menu(parent: QWidget | None, colors: dict) -> QMenu:
    """创建已套用系统样式的右键菜单。"""
    menu = QMenu(parent)
    apply_context_menu(menu, colors)
    return menu


def exec_text_edit_context_menu(edit: QTextEdit, event, colors: dict) -> None:
    """QTextEdit 统一右键菜单：撤销/复制/粘贴等，白底黑字。"""
    menu = make_context_menu(edit, colors)
    undo_act = menu.addAction("撤销")
    undo_act.setEnabled(edit.document().isUndoAvailable())
    redo_act = menu.addAction("重做")
    redo_act.setEnabled(edit.document().isRedoAvailable())
    menu.addSeparator()
    has_sel = edit.textCursor().hasSelection()
    cut_act = menu.addAction("剪切")
    cut_act.setEnabled(has_sel and not edit.isReadOnly())
    copy_act = menu.addAction("复制")
    copy_act.setEnabled(has_sel)
    paste_act = menu.addAction("粘贴")
    paste_act.setEnabled(edit.canPaste() and not edit.isReadOnly())
    menu.addSeparator()
    select_all_act = menu.addAction("全选")
    action = menu.exec(event.globalPos())
    if action == undo_act:
        edit.undo()
    elif action == redo_act:
        edit.redo()
    elif action == cut_act:
        edit.cut()
    elif action == copy_act:
        edit.copy()
    elif action == paste_act:
        edit.paste()
    elif action == select_all_act:
        edit.selectAll()
    event.accept()


def bind_text_edit_context_menu(edit: QTextEdit, colors: dict) -> None:
    """为任意 QTextEdit 绑定统一右键菜单（无需改子类）。"""
    menu_colors = dict(colors)

    def _context_menu_event(event):
        exec_text_edit_context_menu(edit, event, menu_colors)

    edit.contextMenuEvent = _context_menu_event  # type: ignore[method-assign]
