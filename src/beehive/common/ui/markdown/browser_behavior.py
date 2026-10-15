"""只读正文浏览器共用的测量和菜单行为，尺寸参数由控件决定。"""
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication
from beehive.common.ui.styles.menus import make_context_menu


def content_width(browser, *, minimum: int, parent_padding: int) -> int:
    width = browser.viewport().width()
    if width <= 1:
        width = browser.width()
    if width <= 1:
        parent = browser.parentWidget()
        if parent is not None:
            width = parent.width() - parent_padding
    return max(width, minimum)


def fit_content_height(browser, *, width: int, minimum: int, extra: int, cap: int):
    document = browser.document()
    document.setTextWidth(width)
    margins = browser.contentsMargins()
    height = max(int(document.size().height()) + margins.top() + margins.bottom() + extra, minimum)
    if cap > 0:
        height = min(height, cap)
    if abs(height - browser.height()) >= 2:
        browser.setFixedHeight(height)


def exec_readonly_menu(browser, event, colors):
    menu = make_context_menu(browser, colors)
    cursor = browser.textCursor()
    has_selection = cursor.hasSelection()
    has_text = bool(browser.toPlainText())
    copy_action = menu.addAction("复制")
    copy_action.setShortcut("Ctrl+C")
    copy_action.setEnabled(has_selection or has_text)
    link = browser.anchorAt(event.pos())
    copy_link_action = menu.addAction("复制链接") if link else None
    menu.addSeparator()
    select_action = menu.addAction("全选")
    select_action.setEnabled(has_text)
    action = menu.exec(event.globalPos())
    if action == copy_action:
        text = cursor.selectedText().replace("\u2029", "\n") if has_selection else browser.toPlainText()
        QApplication.clipboard().setText(text)
    elif copy_link_action and action == copy_link_action:
        QApplication.clipboard().setText(link)
    elif action == select_action:
        cursor.select(QTextCursor.SelectionType.Document)
        browser.setTextCursor(cursor)
    event.accept()
