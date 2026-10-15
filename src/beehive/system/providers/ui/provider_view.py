"""厂商设置工作区：组合列表、详情表单与编辑控制器。"""
from PySide6.QtWidgets import QWidget, QHBoxLayout
from beehive.common.ui.theme import Theme
from beehive.system.providers.provider_store import ProviderStore
from beehive.system.providers.ui.provider_sidebar import ProviderSidebar
from beehive.system.providers.ui.provider_detail_form import ProviderDetailForm
from beehive.system.providers.ui.provider_editor import ProviderEditorController

class ProviderSettingsWorkspace(QWidget):


    def __init__(self, theme: Theme, store: ProviderStore | None = None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.store = store or ProviderStore()
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        sidebar = ProviderSidebar(theme, self)
        detail = ProviderDetailForm(theme, self.store, self)
        root.addWidget(sidebar)
        root.addWidget(detail, stretch=1)
        self.editor = ProviderEditorController(theme, self.store, sidebar, detail, self)

    def showEvent(self, event):
        super().showEvent(event)
        self.editor._reload_list(keep_current=True)
