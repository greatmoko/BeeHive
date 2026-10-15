"""会话菜单与创建、置顶、重命名、删除操作。"""
from PySide6.QtCore import QObject
from beehive.common.ui.styles.menus import make_context_menu
from beehive.system.providers.provider_store import ProviderStore
from beehive.common.ui.dialogs.message import _confirm
from beehive.common.ui.dialogs.text_input import _ask_text


class ChatSessionActions(QObject):
    def __init__(self, sidebar):
        super().__init__(sidebar)
        self.sidebar = sidebar
        self.theme, self.manager = sidebar.theme, sidebar.manager

    def _on_new(self):
        # 新建对话：预填默认模型（或首个可用模型）
        store = ProviderStore()
        primary = store.first_resolved()
        session = self.manager.create(
            provider=primary.provider_id if primary else "",
            model=primary.model_id if primary else "",
        )
        self.sidebar._selected_id = session.id
        self.sidebar.refresh()
        self.sidebar.session_selected.emit(session.id)

    def _on_context_menu(self, session_id: str, pos):
        c = self.theme.colors
        session = self.manager.get(session_id)
        if not session:
            return

        menu = make_context_menu(self.sidebar, c)

        pin_text = "取消置顶" if session.pinned else "置顶"
        pin_action = menu.addAction(pin_text)
        rename_action = menu.addAction("重命名")
        menu.addSeparator()
        delete_action = menu.addAction("删除")
        delete_action.setProperty("color", c["danger"])

        action = menu.exec(pos)
        if action == pin_action:
            self.manager.toggle_pin(session_id)
            self.sidebar.refresh()
        elif action == rename_action:
            self._show_rename_dialog(session_id, session.title)
        elif action == delete_action:
            self._show_delete_dialog(session_id)

    def _show_rename_dialog(self, session_id, old_title):
        new_title = _ask_text(self.sidebar, self.theme, "重命名对话", "新名称", old_title)
        if new_title:
            self.manager.rename(session_id, new_title)
            self.sidebar.refresh()
            self.sidebar.session_selected.emit(session_id)

    def _show_delete_dialog(self, session_id):
        if not _confirm(self.sidebar, self.theme, "确认删除", "确定删除这个对话吗？删除后无法恢复。"):
            return
        was_selected = session_id == self.sidebar._selected_id
        self.manager.delete(session_id)
        if was_selected:
            remaining = self.manager.list_sorted()
            self.sidebar._selected_id = remaining[0].id if remaining else None
        self.sidebar.refresh()
        self.sidebar.session_deleted.emit()
        if self.sidebar._selected_id:
            self.sidebar.session_selected.emit(self.sidebar._selected_id)
