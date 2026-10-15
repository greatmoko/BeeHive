"""需求输入区布局与按钮装配。"""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout
from beehive.common.ui.styles import apply_icon_btn, apply_textedit
from beehive.common.ui.widgets.model_selector import ModelSelectorButton
from beehive.common.ui.widgets.execution_input import EqButton as _EqButton, InputResizeHandle as _InputResizeHandle

def build_input_layout(bar):
    c = bar.theme.colors
    bar.setStyleSheet(
        f"InputBar {{ background: {c['content_bg']}; border-top: 1px solid {c['border']}; }}"
    )
    layout = QVBoxLayout(bar)
    layout.setContentsMargins(16, 2, 16, 10)
    layout.setSpacing(4)

    # 附件 chip 条（有附件时显示，输入框上方）
    bar._attach_bar = QFrame()
    bar._attach_bar.setVisible(False)
    bar._attach_bar.setStyleSheet("background: transparent; border: none;")
    bar._attach_lay = QHBoxLayout(bar._attach_bar)
    bar._attach_lay.setContentsMargins(0, 0, 0, 0)
    bar._attach_lay.setSpacing(6)
    bar._attach_lay.addStretch()
    layout.addWidget(bar._attach_bar)

    bar._input_resize = _InputResizeHandle(bar.theme)
    bar._input_resize.drag_delta.connect(bar._on_input_resize_delta)
    layout.addWidget(bar._input_resize, alignment=Qt.AlignmentFlag.AlignHCenter)

    # 输入框（上）
    bar._edit = QTextEdit()
    bar._edit.setPlaceholderText(
        "输入需求或修改意见…（Enter 发送，Shift+Enter 换行；可粘贴图片/文件）"
    )
    bar._edit.setAcceptRichText(False)
    bar._edit_min_h = 64
    bar._edit.setMinimumHeight(bar._edit_min_h)
    bar._edit.setFixedHeight(bar._edit_min_h)
    apply_textedit(bar._edit, c)
    bar._edit.installEventFilter(bar)
    layout.addWidget(bar._edit)

    # 功能按钮行（下）
    row = QHBoxLayout()
    row.setSpacing(6)

    open_folder = QPushButton("📁")
    open_folder.setToolTip("打开需求文件夹")
    apply_icon_btn(open_folder, c, size=34)
    open_folder.clicked.connect(bar.open_folder_clicked.emit)
    row.addWidget(open_folder)

    bar._new_conv_btn = QPushButton("💬")
    bar._new_conv_btn.setToolTip("另起对话")
    apply_icon_btn(bar._new_conv_btn, c, size=34)
    bar._new_conv_btn.clicked.connect(bar.new_conversation_clicked.emit)
    row.addWidget(bar._new_conv_btn)

    bar._export_btn = QPushButton("📦")
    bar._export_btn.setToolTip("导出原型")
    apply_icon_btn(bar._export_btn, c, size=34)
    bar._export_btn.clicked.connect(bar.export_clicked.emit)
    row.addWidget(bar._export_btn)

    # 运行中需要禁用的功能按钮（打开文件夹保持可用）
    bar._action_btns = [
        bar._new_conv_btn,
        bar._export_btn,
    ]

    row.addStretch()

    bar._status = QLabel("")
    bar._status.setStyleSheet(
        f"font-size: 11px; color: {c['text_hint']}; background: transparent;"
    )
    row.addWidget(bar._status)

    # 模型选择：TokBee 同款（🤖 + 当前模型名；无模型时显「切换模型」占位），
    # 弹出列表用 ✓ 标记当前模型
    bar._model_combo = ModelSelectorButton(
        bar.theme, width=300, placeholder="切换模型",
        hint_tooltip="正在使用：（默认模型）",
    )
    bar._current_model_pair: tuple[str, str] = ("", "")
    bar._model_combo.model_selected.connect(bar._on_model_selected)
    row.addWidget(bar._model_combo)
    bar.model_combo = bar._model_combo  # 供外部 reload_models / 回显需求模型

    # 预览按钮：与发送按钮同款尺寸，淡黄底黑字
    bar._preview_btn = QPushButton("预览")
    bar._preview_btn.setFixedSize(72, 34)
    bar._preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    bar._preview_btn.setStyleSheet(f"""
        QPushButton {{
            background: {c["warning_bg"]}; color: {c["text"]};
            border: none; border-radius: 6px; font-size: 13px; font-weight: bold;
        }}
        QPushButton:hover {{ background: {c["warning_border"]}; }}
        QPushButton:pressed {{ background: {c["warning_bg"]}; }}
        QPushButton:disabled {{ background: {c['btn_bg']}; color: {c['text_hint']}; }}
    """)
    bar._preview_btn.clicked.connect(bar.preview_clicked.emit)
    row.addWidget(bar._preview_btn)

    # 发送/暂停一体按钮（WokBee _EqButton）：运行中播放均衡器动效，再点即暂停
    bar._send_btn = _EqButton(
        bar.theme, text="发送", width=72,
        bg=c["btn_primary"], bg_hover=c["btn_primary_hover"],
    )
    bar._send_btn.clicked.connect(bar._on_send)
    row.addWidget(bar._send_btn)

    layout.addSpacing(2)
    layout.addLayout(row)
