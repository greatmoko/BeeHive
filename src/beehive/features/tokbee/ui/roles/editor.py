"""按职责组织的 editor。"""
from __future__ import annotations
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QLineEdit, QDialog, QComboBox, QTextEdit
from beehive.common.ui.theme import Theme
from beehive.common.ui.styles.buttons import apply_primary_btn, apply_secondary_btn
from beehive.common.ui.styles.labels import hint_label_qss, section_label_qss
from beehive.common.ui.styles.metrics import DEFAULT_COMBO_HEIGHT
from beehive.common.ui.styles.selectors import apply_combo_popup_style
from beehive.common.ui.widgets.model_selector import ModelSelectorButton
from beehive.system.providers.provider_store import ProviderStore
from beehive.system.providers.provider_models import ResolvedModel
from beehive.features.tokbee.core.ai_role import AIRole, AIRoleManager
from beehive.system.settings.config import Config
from beehive.features.tokbee.ui.roles.generation import _RoleGenWorker


class _AIRoleDialog(QDialog):
    """AI 角色新建 / 编辑弹窗。"""

    def __init__(self, theme: Theme, role: AIRole | None = None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._role = role
        self._gen_worker: _RoleGenWorker | None = None
        self._provider_store = ProviderStore()
        self._role_manager = AIRoleManager()
        self._config = Config()
        self._build()

    def _build(self):
        c = self.theme.colors
        editing = self._role is not None

        self.setWindowTitle("编辑角色" if editing else "新建角色")
        self.setFixedSize(460, 620)
        self.setStyleSheet(f"background: {c['content_bg']};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(10)

        # ── 角色名称 + AI 创作按钮 ──
        name_lbl = QLabel("角色名称")
        name_lbl.setStyleSheet(section_label_qss(c))
        layout.addWidget(name_lbl)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("例如：翻译助手")
        if editing:
            self._name_input.setText(self._role.name)
        name_row.addWidget(self._name_input, stretch=1)

        self._ai_btn = QPushButton("✨ AI 创作")
        apply_primary_btn(self._ai_btn, c)
        self._ai_btn.clicked.connect(self._on_ai_generate)
        name_row.addWidget(self._ai_btn)

        layout.addLayout(name_row)

        # ── AI 创作设置：模型选择 + 角色选择 ──
        ai_settings_row = QHBoxLayout()
        ai_settings_row.setSpacing(12)

        model_col = QVBoxLayout()
        model_col.setSpacing(2)
        model_lbl = QLabel("AI 模型")
        model_lbl.setStyleSheet(hint_label_qss(c))
        model_col.addWidget(model_lbl)

        self._model_combo = ModelSelectorButton(
            self.theme, width=300, placeholder="未配置模型", hint_tooltip="AI 模型",
        )
        try:
            models = self._provider_store.list_selectable_models()
        except Exception:
            models = []
        options = [
            (f"{m.provider_name} / {m.model_id}", (m.provider_id, m.model_id))
            for m in models
        ]
        # 回显上次使用；未记录或已失效时回退第一项（与旧下拉行为一致）
        current = options[0][1] if options else ("", "")
        last_key = self._config.get("role_gen.model_key", "")
        if last_key and "|" in last_key:
            pair = tuple(last_key.split("|", 1))
            if any(p == pair for _, p in options):
                current = pair
        self._model_combo.set_options(options, current)
        model_col.addWidget(self._model_combo)
        ai_settings_row.addLayout(model_col, stretch=1)

        role_col = QVBoxLayout()
        role_col.setSpacing(2)
        role_lbl = QLabel("创作角色")
        role_lbl.setStyleSheet(hint_label_qss(c))
        role_col.addWidget(role_lbl)

        self._role_combo = QComboBox()
        apply_combo_popup_style(self._role_combo, c, rounded=True, fixed_height=DEFAULT_COMBO_HEIGHT)
        self._role_combo.addItem("默认（Prompt 专家）", "")
        roles = self._role_manager.list_all()
        last_role_id = self._config.get("role_gen.role_id", "")
        role_select_idx = 0
        for i, r in enumerate(roles):
            self._role_combo.addItem(r.name, r.id)
            if last_role_id and r.id == last_role_id:
                role_select_idx = i + 1
        self._role_combo.setCurrentIndex(role_select_idx)
        role_col.addWidget(self._role_combo)
        ai_settings_row.addLayout(role_col, stretch=1)

        layout.addLayout(ai_settings_row)

        # ── 角色描述 ──
        desc_lbl = QLabel("角色描述")
        desc_lbl.setStyleSheet(section_label_qss(c))
        layout.addWidget(desc_lbl)

        hint = QLabel("即 System Prompt，用于定义 AI 的角色和行为方式")
        hint.setStyleSheet(hint_label_qss(c))
        layout.addWidget(hint)

        self._desc_input = QTextEdit()
        self._desc_input.setPlaceholderText("例如：你是一个专业的中英翻译助手，请将用户输入的内容翻译成对应语言...")
        self._desc_input.setMinimumHeight(260)
        self._desc_input.setStyleSheet(f"""
            QTextEdit {{
                background: {c["input_bg"]}; border: 1px solid {c["input_border"]};
                border-radius: 6px; padding: 6px 10px; color: {c["text"]}; font-size: 13px;
            }}
            QTextEdit:focus {{ border-color: {c["input_focus_border"]}; }}
        """)
        if editing:
            self._desc_input.setPlainText(self._role.description)
        layout.addWidget(self._desc_input)

        layout.addStretch()

        # ── 底部按钮 ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        apply_secondary_btn(cancel_btn, c)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("保存")
        apply_primary_btn(save_btn, c)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

    def _get_selected_model(self) -> ResolvedModel | None:
        pid, mid = self._model_combo.current_model()
        if not mid:
            return None
        return self._provider_store.resolve(pid, mid)

    def _get_selected_role_prompt(self) -> str:
        role_id = self._role_combo.currentData()
        if not role_id:
            return ""
        role = self._role_manager.get(role_id)
        return role.description if role else ""

    def _on_ai_generate(self):
        role_name = self._name_input.text().strip()
        if not role_name:
            self._name_input.setFocus()
            return

        model = self._get_selected_model()
        if not model or not model.api_host:
            self._desc_input.setPlainText("请先在「AI配置 → 厂商设置」中配置模型")
            return

        if self._gen_worker and self._gen_worker.isRunning():
            self._gen_worker.wait(3000)

        pid, mid = self._model_combo.current_model()
        self._config.set("role_gen.model_key", f"{pid}|{mid}" if mid else "")
        self._config.set("role_gen.role_id", self._role_combo.currentData() or "")
        self._config.save()

        self._desc_input.clear()
        self._desc_input.setPlaceholderText("AI 正在创作中，请稍候...")
        self._ai_btn.setEnabled(False)
        self._ai_btn.setText("生成中…")
        self._name_input.setEnabled(False)

        system_prompt = self._get_selected_role_prompt()
        self._gen_worker = _RoleGenWorker(model, role_name, system_prompt, parent=self)
        self._gen_worker.done.connect(self._on_gen_done)
        self._gen_worker.error.connect(self._on_gen_error)
        self._gen_worker.start()

    def _on_gen_done(self, content: str):
        self._desc_input.setPlainText(content)
        self._desc_input.setPlaceholderText("例如：你是一个专业的中英翻译助手，请将用户输入的内容翻译成对应语言...")
        self._ai_btn.setEnabled(True)
        self._ai_btn.setText("✨ AI 创作")
        self._name_input.setEnabled(True)

    def _on_gen_error(self, msg: str):
        self._ai_btn.setEnabled(True)
        self._ai_btn.setText("✨ AI 创作")
        self._name_input.setEnabled(True)
        self._desc_input.setPlaceholderText("例如：你是一个专业的中英翻译助手，请将用户输入的内容翻译成对应语言...")
        if not self._desc_input.toPlainText().strip():
            self._desc_input.setPlainText(f"生成失败: {msg}")

    def _on_save(self):
        if not self._name_input.text().strip():
            self._name_input.setFocus()
            return
        if self._gen_worker and self._gen_worker.isRunning():
            self._gen_worker.wait(3000)
        self.accept()

    def reject(self):
        if self._gen_worker and self._gen_worker.isRunning():
            self._gen_worker.wait(3000)
        super().reject()

    def get_data(self) -> dict:
        return {
            "name": self._name_input.text().strip(),
            "description": self._desc_input.toPlainText().strip(),
        }
