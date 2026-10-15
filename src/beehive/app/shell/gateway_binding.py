"""将业务项目选择注入系统通道页面。"""
from PySide6.QtWidgets import QComboBox, QLabel
from beehive.common.ui.styles.selectors import apply_form_combo
from beehive.system.gateway.ui.styles import _card_frame


class ProjectBindingCard:
    """绑定控件与刷新抑制标记归卡片自身，不占用系统页面状态。"""
    def __init__(self, theme, store, projects, channel):
        self.store = store
        self.projects = projects
        self.channel = channel
        self.syncing = False
        self.widget, layout = _card_frame(theme, '项目绑定')
        hint = QLabel('无前缀消息路由到默认项目；可用 @项目ID 切换指定项目；#help 查看全部指令。')
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.colors['text_hint']}; background: transparent; border: none; font-size: 12px;")
        layout.addWidget(hint)
        self.combo = QComboBox()
        apply_form_combo(self.combo, theme.colors)
        self.combo.currentIndexChanged.connect(self.changed)
        layout.addWidget(self.combo)

    def changed(self):
        if self.syncing:
            return
        cfg = self.store.get_config()
        cfg.set_default_project(self.channel, self.combo.currentData() or '')
        self.store.save_config(cfg)

    def refresh(self):
        cfg = self.store.get_config()
        self.syncing = True
        try:
            self.combo.clear()
            self.combo.addItem('（未绑定）', '')
            for project in self.projects.list_projects():
                self.combo.addItem(f'{project.title}（{project.id}）', project.id)
            index = self.combo.findData(cfg.default_project_for(self.channel))
            if index >= 0:
                self.combo.setCurrentIndex(index)
        finally:
            self.syncing = False
