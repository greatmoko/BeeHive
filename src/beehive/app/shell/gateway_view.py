"""应用负责系统通道 UI 与 WokBee 项目绑定的装配。"""
from beehive.app.shell.gateway_binding import ProjectBindingCard
from beehive.features.wokbee.gateway.manager import GatewayManager
from beehive.system.gateway.ui.workspace import GatewayWorkspace


def create_gateway_page(theme, manager=None):
    manager = manager or GatewayManager()

    def binding_factory(channel):
        return ProjectBindingCard(theme, manager.store, manager.project_store, channel)

    page = GatewayWorkspace(theme, manager.connection, binding_factory=binding_factory)
    page.manager_owner = manager
    return page
