"""模型选择、会话参数编辑与提示框的交互协调。"""
from PySide6.QtCore import QObject, Qt
from beehive.common.ui.dialogs.compact import show_compact_tip
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox
from beehive.common.ui.styles.buttons import apply_primary_btn, apply_secondary_btn
from beehive.common.ui.styles.inputs import rounded_spin_qss
from beehive.common.ui.styles.labels import hint_label_qss, section_label_qss
from beehive.system.providers.provider_store import ProviderStore


class ChatOptionsController(QObject):
    def __init__(self, view):
        super().__init__(view)
        self.view, self.vm, self.theme = view, view.vm, view.theme

    def tip(self, message: str):
        show_compact_tip(self.view, self.theme, message)

    def model_options(self):
        """供模型选择按钮点击时拉取的可选列表 [(label, (provider_id, model_id))]。"""
        try:
            models = ProviderStore().list_selectable_models()
        except Exception:
            return []
        return [
            (f"{m.provider_name} / {m.model_id}", (m.provider_id, m.model_id))
            for m in models
        ]

    def session_settings(self):
        """会话级配置：允许为当前对话单独覆盖上下文条数，默认取全局默认值。"""
        if not self.vm.session:
            return
        session = self.vm.session
        c = self.theme.colors
        params = session.get_params()
        defaults = self.vm.manager.session_defaults.get()
        global_ctx = defaults.max_context_message_count

        dlg = QDialog(self.view)
        dlg.setWindowTitle("会话设置")
        dlg.setFixedSize(420, 220)
        dlg.setStyleSheet(f"background: {c['content_bg']};")
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(10)

        lbl = QLabel("上下文消息数")
        lbl.setStyleSheet(section_label_qss(c))
        layout.addWidget(lbl)

        hist_hint = QLabel(
            "携带最近多少条消息（soft limit）；设很大表示尽量不按条数截断。\n"
            f"全局默认：{global_ctx} 条。"
        )
        hist_hint.setWordWrap(True)
        hist_hint.setStyleSheet(hint_label_qss(c))
        layout.addWidget(hist_hint)

        hist_box = QSpinBox()
        hist_box.setRange(0, 100000)
        hist_box.setSingleStep(2)
        hist_box.setValue(params.max_context_message_count)
        hist_box.setFixedWidth(200)
        hist_box.setStyleSheet(rounded_spin_qss(self.theme.colors))
        layout.addWidget(hist_box, alignment=Qt.AlignmentFlag.AlignLeft)

        layout.addStretch()

        def set_global():
            hist_box.setValue(global_ctx)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        use_default_btn = QPushButton("沿用全局默认")
        use_default_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_secondary_btn(use_default_btn, c)
        use_default_btn.clicked.connect(set_global)
        btn_row.addWidget(use_default_btn)

        cancel_btn = QPushButton("取消")
        apply_secondary_btn(cancel_btn, c)
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("保存")
        apply_primary_btn(save_btn, c)
        save_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            params.max_context_message_count = hist_box.value()
            session.set_params(params)
            self.vm.manager.save()
            self.tip(f"已更新上下文条数：{hist_box.value()} 条")
