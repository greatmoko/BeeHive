"""厂商详情表单，包含连接字段、操作按钮和模型列表。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QLabel, QLineEdit, QPushButton
from beehive.common.ui.styles.buttons import apply_danger_btn, apply_secondary_btn
from beehive.common.ui.styles.inputs import apply_lineedit
from beehive.common.ui.styles.labels import section_label_qss, title_label_qss
from beehive.system.providers.provider_models import ProviderSettings
from beehive.system.providers.ui.provider_model_list import ProviderModelList

class ProviderDetailForm(QWidget):
    changed = Signal()
    editing_finished = Signal()
    fetch_requested = Signal()
    add_model_requested = Signal()
    clear_models_requested = Signal()
    reset_requested = Signal()
    remove_requested = Signal()


    def __init__(self, theme, store, parent=None):
        super().__init__(parent)
        c = theme.colors
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._right_stack = QStackedWidget()
        # 未选择厂商时的提示页
        empty_page = QWidget()
        el = QVBoxLayout(empty_page)
        el.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_msg = QLabel("尚未选择厂商\n请从左侧添加或选择一个厂商进行配置")
        empty_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_msg.setStyleSheet(f"font-size: 14px; color: {c['text_hint']};")
        el.addWidget(empty_msg)
        self._empty_default_lbl = QLabel("")
        self._empty_default_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_default_lbl.setStyleSheet(
            f"font-size: 12px; color: {c['text_secondary']}; margin-top: 12px;"
        )
        el.addWidget(self._empty_default_lbl)
        self._right_stack.addWidget(empty_page)

        # 厂商详情页
        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(28, 24, 28, 24)
        right_l.setSpacing(12)

        self._title = QLabel("厂商设置")
        self._title.setStyleSheet(title_label_qss(c))
        right_l.addWidget(self._title)

        self._notes = QLabel("")
        self._notes.setWordWrap(True)
        self._notes.setStyleSheet(f"font-size: 12px; color: {c['text_hint']};")
        right_l.addWidget(self._notes)

        self._default_hint = QLabel("")
        self._default_hint.setWordWrap(True)
        self._default_hint.setStyleSheet(
            f"font-size: 12px; color: {c['text_secondary']};"
        )
        right_l.addWidget(self._default_hint)

        key_lbl = QLabel("API Key")
        key_lbl.setStyleSheet(section_label_qss(c))
        right_l.addWidget(key_lbl)
        self._key_edit = QLineEdit()
        self._key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_edit.setPlaceholderText("输入 API Key（本地可留空）")
        apply_lineedit(self._key_edit, c)
        self._key_edit.textChanged.connect(self.changed)
        self._key_edit.editingFinished.connect(self.editing_finished)
        right_l.addWidget(self._key_edit)

        host_lbl = QLabel("API Host")
        host_lbl.setStyleSheet(section_label_qss(c))
        right_l.addWidget(host_lbl)
        self._host_edit = QLineEdit()
        self._host_edit.setPlaceholderText("https://api.example.com/v1")
        apply_lineedit(self._host_edit, c)
        self._host_edit.textChanged.connect(self.changed)
        self._host_edit.editingFinished.connect(self.editing_finished)
        right_l.addWidget(self._host_edit)

        model_row = QHBoxLayout()
        model_lbl = QLabel("模型列表")
        model_lbl.setStyleSheet(section_label_qss(c))
        model_row.addWidget(model_lbl)
        model_row.addStretch()

        self._query_btn = QPushButton("拉取远程模型")
        apply_secondary_btn(self._query_btn, c, height=30)
        self._query_btn.clicked.connect(self.fetch_requested)
        model_row.addWidget(self._query_btn)

        add_model_btn = QPushButton("+ 添加")
        apply_secondary_btn(add_model_btn, c, height=30)
        add_model_btn.clicked.connect(self.add_model_requested)
        model_row.addWidget(add_model_btn)

        clear_models_btn = QPushButton("清空模型 ID")
        clear_models_btn.setToolTip("一键清空当前厂商的全部模型 ID")
        apply_danger_btn(clear_models_btn, c, height=30)
        clear_models_btn.clicked.connect(self.clear_models_requested)
        model_row.addWidget(clear_models_btn)
        right_l.addLayout(model_row)

        self.models = ProviderModelList(theme, store, right)
        right_l.addWidget(self.models, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        reset_btn = QPushButton("恢复默认 Host")
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.setFixedHeight(34)
        reset_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {c["text_secondary"]};
                border: 1px solid {c["border"]}; border-radius: 6px; padding: 0 14px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {c["subnav_hover"]}; }}
        """)
        reset_btn.clicked.connect(self.reset_requested)
        self._reset_btn = reset_btn
        btn_row.addWidget(reset_btn)

        del_btn = QPushButton("从列表移除")
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setFixedHeight(34)
        del_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {c["danger"]};
                border: 1px solid {c["border"]}; border-radius: 6px; padding: 0 14px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {c["subnav_hover"]}; }}
        """)
        del_btn.clicked.connect(self.remove_requested)
        self._del_btn = del_btn
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        right_l.addLayout(btn_row)

        self._right_stack.addWidget(right)
        layout.addWidget(self._right_stack)

    def collect_settings(self):
        return ProviderSettings(
            api_key=self._key_edit.text().strip(),
            api_host=self._host_edit.text().strip(),
            models=self.models.collect_models(),
        )
