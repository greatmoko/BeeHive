"""操作栏布局装配。"""
from __future__ import annotations
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout
from beehive.common.ui.styles import apply_textedit, apply_icon_btn
from beehive.common.ui.styles.menus import bind_text_edit_context_menu
from beehive.common.ui.widgets.context_usage import ContextUsageRing
from beehive.common.ui.widgets.execution_input import EqButton as _EqButton, InputResizeHandle as _InputResizeHandle
from beehive.common.ui.widgets.model_selector import ModelSelectorButton
def build_action_bar(self):
    c = self.theme.colors
    self.setStyleSheet(f"""
            _ActionBar {{
                background: {c["content_bg"]};
                border-top: 1px solid {c["border"]};
            }}
        """)
    layout = QVBoxLayout(self)
    layout.setContentsMargins(16, 2, 16, 12)
    layout.setSpacing(4)

    self._approval_bar = QFrame()
    self._approval_bar.setVisible(False)
    self._approval_bar.setStyleSheet(f"""
            QFrame {{
                background: {c["warning_bg"]};
                border: 1px solid {c["warning_border"]};
                border-radius: 8px;
            }}
        """)
    ap_lay = QVBoxLayout(self._approval_bar)
    ap_lay.setContentsMargins(12, 8, 12, 8)
    ap_lay.setSpacing(6)
    self._approval_label = QLabel("等待审批…")
    self._approval_label.setWordWrap(True)
    self._approval_label.setStyleSheet(f"font-size: 12px; color: {c['text']}; background: transparent; border: none;")
    ap_lay.addWidget(self._approval_label)
    ap_btns = QHBoxLayout()
    ap_btns.addStretch()
    reject_btn = QPushButton("拒绝")
    reject_btn.setFixedHeight(30)  # 审批条紧凑例外
    reject_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    reject_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c["danger"]}; color: white;
                border: none; border-radius: 6px; padding: 0 14px;
            }}
            QPushButton:hover {{ background: {c["danger_hover"]}; }}
        """)
    reject_btn.clicked.connect(self.reject_clicked.emit)
    approve_btn = QPushButton("全部通过")
    approve_btn.setFixedHeight(30)  # 审批条紧凑例外
    approve_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    approve_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_primary"]}; color: white;
                border: none; border-radius: 6px; padding: 0 14px;
            }}
            QPushButton:hover {{ background: {c["btn_primary_hover"]}; }}
        """)
    approve_btn.clicked.connect(self.approve_clicked.emit)
    ap_btns.addWidget(reject_btn)
    ap_btns.addWidget(approve_btn)
    ap_lay.addLayout(ap_btns)
    layout.addWidget(self._approval_bar)

    self._attach_bar = QFrame()
    self._attach_bar.setVisible(False)
    self._attach_bar.setStyleSheet("background: transparent; border: none;")
    self._attach_lay = QHBoxLayout(self._attach_bar)
    self._attach_lay.setContentsMargins(0, 0, 0, 0)
    self._attach_lay.setSpacing(6)
    self._attach_lay.addStretch()
    layout.addWidget(self._attach_bar)

    self._input_resize = _InputResizeHandle(self.theme)
    self._input_resize.drag_delta.connect(self._on_input_resize_delta)
    layout.addWidget(self._input_resize, alignment=Qt.AlignmentFlag.AlignHCenter)

    self._input = QTextEdit()
    bind_text_edit_context_menu(self._input, c)
    self._input.setPlaceholderText(
        "输入提问或指令…（Enter 发送，Shift+Enter 换行；发送=完整能力，运行=经验管线）"
    )
    self._input_min_h = 72
    self._input.setMinimumHeight(self._input_min_h)
    self._input.setFixedHeight(self._input_min_h)
    apply_textedit(self._input, c)
    self._input.textChanged.connect(self.draft_changed.emit)
    self._input.installEventFilter(self)
    layout.addWidget(self._input)

    row = QHBoxLayout()
    row.setSpacing(6)

    for icon, tip, slot in (
        ("📁", "打开目录", self.open_folder_clicked.emit),
        ("⬆️", "上传文件", self.upload_clicked.emit),
        ("🗄️", "归档（仅保留最新经验）", self.archive_clicked.emit),
    ):
        btn = QPushButton(icon)
        btn.setToolTip(tip)
        apply_icon_btn(btn, c, size=34)
        btn.clicked.connect(slot)
        row.addWidget(btn)

    row.addStretch()

    self._model_combo = ModelSelectorButton(
        self.theme, width=300, placeholder="未配置模型", hint_tooltip="切换本项目模型"
    )
    self._model_combo.model_selected.connect(self.model_changed.emit)
    row.addWidget(self._model_combo)

    self._ctx_ring = ContextUsageRing(self.theme)
    self._ctx_ring.setToolTip("上下文用量")
    self._ctx_ring.set_ring_enabled(False)
    row.addWidget(self._ctx_ring)

    self._run_btn = _EqButton(
        self.theme, text="运行", width=59, bg=c["warning"], bg_hover=c["warning_border"]
    )
    self._run_btn.clicked.connect(self._on_run_btn_clicked)
    row.addWidget(self._run_btn)

    self._send_btn = _EqButton(
        self.theme, text="发送", width=72, bg=c["btn_primary"], bg_hover=c["btn_primary_hover"]
    )
    self._send_btn.clicked.connect(self._on_send)
    row.addWidget(self._send_btn)
    layout.addSpacing(4)
    layout.addLayout(row)

    self.reload_models()
    QTimer.singleShot(0, self._sync_input_resize_width)
