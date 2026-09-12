"""DeziBee 工作区（右侧）：需求信息区 + 交互记录区 + 功能及用户输入区。

功能及用户输入区布局与 WokBee 一致：输入框在上，功能按钮行在下方
（打开文件夹 / 总结上下文 / 另起对话 / 预览 | 状态 · AI模型 · 发送）。
发送按钮与 WokBee 同款：空闲=发送，运行中显示均衡器动效、再点一次即暂停。
支持粘贴/发送图片与文件附件（与 WokBee 同一套 chip UI 与落盘逻辑）。
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QBuffer, QEvent, QFileInfo, QIODevice, Qt, Signal
from PySide6.QtGui import QImage, QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileIconProvider,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from tokbee.ui.combo_style import apply_combo_popup_style
from tokbee.ui.styles.theme import Theme
from wokbee.ui.action_bar import _EqButton, _AttachmentChip, _is_image_file, _sanitize_name

from dezibee.core.models import Requirement
from dezibee.core.services import model_options
from dezibee.ui.info_panel import ReqInfoPanel
from dezibee.ui.web_chat import DeziBeeChatLog

_IMAGE_EXTS = _is_image_file  # 复用 WokBee 判定（函数形式保持一致命名）


class InputBar(QFrame):
    """功能及用户输入区：输入框在上，功能按钮在下方（参考 WokBee 布局）。"""

    send_clicked = Signal(str, list)  # text, attachments
    pause_clicked = Signal()
    open_folder_clicked = Signal()
    preview_clicked = Signal()
    export_clicked = Signal()
    summarize_clicked = Signal()
    new_conversation_clicked = Signal()
    model_changed = Signal(str, str)  # provider_id, model_id

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._running = False
        self._attachments: list[dict] = []
        self._uploads_root: Path | None = None  # 由 view 按需求注入（需求目录/uploads）
        c = theme.colors
        self.setStyleSheet(
            f"InputBar {{ background: {c['content_bg']}; border-top: 1px solid {c['border']}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(6)

        # 附件 chip 条（有附件时显示，输入框上方）
        self._attach_bar = QFrame()
        self._attach_bar.setVisible(False)
        self._attach_bar.setStyleSheet("background: transparent; border: none;")
        self._attach_lay = QHBoxLayout(self._attach_bar)
        self._attach_lay.setContentsMargins(0, 0, 0, 0)
        self._attach_lay.setSpacing(6)
        self._attach_lay.addStretch()
        layout.addWidget(self._attach_bar)

        # 输入框（上）
        self._edit = QTextEdit()
        self._edit.setPlaceholderText(
            "输入需求或修改意见…（Enter 发送，Shift+Enter 换行；可粘贴图片/文件）"
        )
        self._edit.setAcceptRichText(False)
        self._edit.setFixedHeight(64)
        self._edit.setStyleSheet(f"""
            QTextEdit {{
                background: {c["input_bg"]}; color: {c["text"]};
                border: 1px solid {c["input_border"]}; border-radius: 8px;
                padding: 6px 10px; font-size: 13px;
            }}
            QTextEdit:focus {{ border: 1px solid {c["input_focus_border"]}; }}
        """)
        self._edit.installEventFilter(self)
        layout.addWidget(self._edit)

        # 功能按钮行（下）
        row = QHBoxLayout()
        row.setSpacing(6)

        self._icon_btn_style = f"""
            QPushButton {{
                background: {c["btn_bg"]}; color: {c["text"]};
                border: none; border-radius: 6px; font-size: 16px;
                padding: 0;
            }}
            QPushButton:hover {{ background: {c["btn_hover"]}; }}
            QPushButton:pressed {{ background: {c["accent_light"]}; }}
        """

        open_folder = QPushButton("📁")
        open_folder.setToolTip("打开需求文件夹")
        open_folder.setFixedSize(34, 34)
        open_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        open_folder.setStyleSheet(self._icon_btn_style)
        open_folder.clicked.connect(self.open_folder_clicked.emit)
        row.addWidget(open_folder)

        self._summarize_btn = QPushButton("📝")
        self._summarize_btn.setToolTip("总结上下文")
        self._summarize_btn.setFixedSize(34, 34)
        self._summarize_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._summarize_btn.setStyleSheet(self._icon_btn_style)
        self._summarize_btn.clicked.connect(self.summarize_clicked.emit)
        row.addWidget(self._summarize_btn)

        self._new_conv_btn = QPushButton("💬")
        self._new_conv_btn.setToolTip("另起对话")
        self._new_conv_btn.setFixedSize(34, 34)
        self._new_conv_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_conv_btn.setStyleSheet(self._icon_btn_style)
        self._new_conv_btn.clicked.connect(self.new_conversation_clicked.emit)
        row.addWidget(self._new_conv_btn)

        self._preview_btn = QPushButton("🌐")
        self._preview_btn.setToolTip("预览")
        self._preview_btn.setFixedSize(34, 34)
        self._preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._preview_btn.setStyleSheet(self._icon_btn_style)
        self._preview_btn.clicked.connect(self.preview_clicked.emit)
        row.addWidget(self._preview_btn)

        self._export_btn = QPushButton("📦")
        self._export_btn.setToolTip("导出原型")
        self._export_btn.setFixedSize(34, 34)
        self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_btn.setStyleSheet(self._icon_btn_style)
        self._export_btn.clicked.connect(self.export_clicked.emit)
        row.addWidget(self._export_btn)

        # 运行中需要禁用的功能按钮（打开文件夹保持可用）
        self._action_btns = [
            self._summarize_btn,
            self._new_conv_btn,
            self._preview_btn,
            self._export_btn,
        ]

        row.addStretch()

        self._status = QLabel("")
        self._status.setStyleSheet(
            f"font-size: 11px; color: {c['text_hint']}; background: transparent;"
        )
        row.addWidget(self._status)

        model_lbl = QLabel("AI模型")
        model_lbl.setStyleSheet(
            f"font-size: 12px; color: {c['text_secondary']}; background: transparent;"
        )
        row.addWidget(model_lbl)

        # 模型下拉：与 WokBee 操作栏同款样式（内联 QSS + popup 委托）
        self._model_combo = QComboBox()
        self._model_combo.setFixedHeight(34)
        self._model_combo.setMinimumWidth(200)
        self._model_combo.setMaximumWidth(320)
        self._model_combo.setToolTip("切换本需求使用的 AI 模型")
        self._model_combo.setStyleSheet(f"""
            QComboBox {{
                background: {c["input_bg"]}; color: {c["text"]};
                border: 1px solid {c["input_border"]}; border-radius: 6px;
                padding: 0 10px; font-size: 12px;
            }}
            QComboBox:hover {{ border: 1px solid {c["input_focus_border"]}; }}
            QComboBox:disabled {{ color: {c["text_hint"]}; }}
            QComboBox::drop-down {{ border: none; width: 22px; }}
        """)
        apply_combo_popup_style(self._model_combo, c)
        self._model_combo.currentIndexChanged.connect(self._on_model_selected)
        row.addWidget(self._model_combo)
        self.model_combo = self._model_combo  # 供外部 reload_models / 回显需求模型

        # 发送/暂停一体按钮（WokBee _EqButton）：运行中播放均衡器动效，再点即暂停
        self._send_btn = _EqButton(
            self.theme, text="发送", width=72, bg="#07c160", bg_hover="#06ad56"
        )
        self._send_btn.clicked.connect(self._on_send)
        row.addWidget(self._send_btn)

        layout.addLayout(row)
        self.reload_models()

    # ── 附件（与 WokBee 同一套：粘贴/去重/落盘/取走） ─────
    def set_uploads_root(self, root: str | Path | None):
        """由 view 在需求切换时注入：粘贴的图片/文件立即保存到 <需求目录>/uploads/。"""
        self._uploads_root = Path(root) if root else None

    def _add_attachment(self, item: dict):
        # 唯一性：同一个本地文件不重复追加；剪贴板图片无源路径，允许连续粘贴多张
        for exists in self._attachments:
            src = item.get("path")
            if src and exists.get("path") == src:
                return
        self._persist_inline(item)
        self._attachments.append(item)
        self._refresh_attach_bar()

    def _refresh_attach_bar(self):
        # 清空除 stretch 外的子控件
        while self._attach_lay.count() > 1:
            w = self._attach_lay.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
        for item in self._attachments:
            chip = _AttachmentChip(item, self.theme)
            chip.remove_clicked.connect(
                lambda it, c=chip: self._remove_attachment(it)
            )
            # 插到 stretch 之前
            self._attach_lay.insertWidget(self._attach_lay.count() - 1, chip)
        self._attach_bar.setVisible(bool(self._attachments))
        self._attach_bar.adjustSize()

    def _remove_attachment(self, item: dict):
        for i, it in enumerate(self._attachments):
            if it is item:
                self._attachments.pop(i)
                break
        self._refresh_attach_bar()

    def _persist_inline(self, item: dict):
        """图片/文件（无本地 path）写盘到 uploads/，返回 path；失败则原样。"""
        data = item.get("data")
        display = item.get("display_name") or "attachment"
        root = self._uploads_root
        name = _sanitize_name(display)
        source = item.get("path")
        if root is None:
            return source
        try:
            root.mkdir(parents=True, exist_ok=True)
            target = root / name
            if target.exists():
                target = root / (
                    f"{target.stem}_{time.strftime('%Y%m%d_%H%M%S')}"
                    f"_{time.time_ns() % 1_000_000:06d}{target.suffix}"
                )
            if source is not None and Path(str(source)).exists():
                import shutil

                shutil.copy2(str(source), str(target))
            elif data is not None:
                target.write_bytes(data)
            else:
                return source
            item["path"] = target
            item["display_name"] = target.name
            return target
        except OSError:
            return source

    def _take_attachments(self) -> list[dict]:
        """返回附件列表（供发送取走），随后清空 chip 条。"""
        items = list(self._attachments)
        for it in items:
            self._persist_inline(it)
        self._attachments.clear()
        self._refresh_attach_bar()
        return items

    def _handle_paste(self) -> bool:
        """Ctrl+V / 剪贴板事件：识别图片与文件附件（与 WokBee 同逻辑）。"""
        from PySide6.QtWidgets import QApplication

        cb = QApplication.clipboard()
        mime = cb.mimeData()
        got = False
        # 文件粘贴优先使用本地 URL，保留原始文件名（包括复制的图片文件）
        if mime.hasUrls():
            for url in mime.urls():
                if not url.isLocalFile():
                    continue
                p = Path(url.toLocalFile())
                if not p.exists() or not p.is_file():
                    continue
                self._add_attachment(
                    {
                        "kind": "image" if _is_image_file(p) else "file",
                        "display_name": p.name,
                        "data": None,
                        "path": p,
                    }
                )
            got = True
        # 纯截图/剪贴板图片没有原始文件名，只能生成唯一名称
        elif mime.hasImage():
            image = cb.image()
            if not image.isNull():
                img = image if isinstance(image, QImage) else image.toImage()
                try:
                    buf = QBuffer()
                    buf.open(QIODevice.WriteOnly)
                    suffix = "png"
                    if not img.save(buf, "PNG"):
                        img.save(buf, "JPG")
                        suffix = "jpg"
                    data = bytes(buf.data())
                    self._add_attachment(
                        {
                            "kind": "image",
                            "display_name": (
                                f"pasted_image_{time.strftime('%Y%m%d_%H%M%S')}"
                                f"_{time.time_ns() % 1_000_000:06d}.png"
                                if suffix == "png"
                                else f"pasted_image_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
                            ),
                            "data": data,
                            "path": None,
                        }
                    )
                except Exception:
                    pass
                got = True
        return got

    def eventFilter(self, obj, event):
        if obj is self._edit:
            if event.type() == QEvent.Type.KeyPress:
                key_event = event
                if isinstance(key_event, QKeyEvent):
                    if (
                        key_event.key() == Qt.Key.Key_V
                        and key_event.modifiers() & Qt.KeyboardModifier.ControlModifier
                    ):
                        return self._handle_paste()
                    if key_event.key() in (
                        Qt.Key.Key_Return,
                        Qt.Key.Key_Enter,
                    ):
                        if key_event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                            return False  # Shift+Enter 换行
                        self._on_send()
                        return True
            elif event.type() == QEvent.Type.Clipboard:
                # 某些平台会在粘贴前发送 Clipboard 事件；只有识别到附件时才拦截
                if self._handle_paste():
                    return True
        return super().eventFilter(obj, event)

    # ── 发送 / 暂停（WokBee 交互：运行中再点即暂停） ──────
    def _on_send(self):
        if self._running:
            self.pause_clicked.emit()
            return
        text = self._edit.toPlainText().strip()
        if not text and not self._attachments:
            return
        self.send_clicked.emit(text, self._take_attachments())
        self._edit.clear()

    def set_running(self, running: bool):
        """运行态：发送按钮切均衡器动效（点击=暂停），功能按钮与模型切换禁用。"""
        self._running = running
        self._send_btn.set_spinning(running)
        for btn in self._action_btns:
            btn.setEnabled(not running)
        self._model_combo.setEnabled(not running)
        self._status.setText("Agent 处理中…" if running else "")

    def focus_input(self):
        self._edit.setFocus()

    # ── 模型选择 ─────────────────────────────────────────
    def reload_models(self, selected: tuple[str, str] | None = None):
        """刷新模型下拉；selected=(provider_id, model_id) 需保持选中。"""
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.addItem("（默认模型）", ("", ""))
        index = 0
        for i, (label, pair) in enumerate(model_options(), start=1):
            self._model_combo.addItem(label, pair)
            if selected and pair == tuple(selected):
                index = i
        self._model_combo.setCurrentIndex(index)
        self._model_combo.blockSignals(False)

    def _on_model_selected(self):
        pair = self._model_combo.currentData() or ("", "")
        self.model_changed.emit(str(pair[0]), str(pair[1]))


class DeziBeeWorkspace(QWidget):
    """右侧工作区：需求信息区 / 交互记录区 / 功能及用户输入区（顺序固定）。"""

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._req: Requirement | None = None
        self._build()

    def _build(self):
        self.setStyleSheet(
            f"DeziBeeWorkspace {{ background: {self.theme.colors['content_bg']}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 0)
        layout.setSpacing(8)

        # 1. 需求信息区
        self.info_panel = ReqInfoPanel(self.theme)
        layout.addWidget(self.info_panel)

        # 2. 交互记录区（可伸缩，WokBee 网页聊天组件）
        self.chat_log = DeziBeeChatLog(self.theme)
        layout.addWidget(self.chat_log, 1)

        # 3+4. 功能及用户输入区（输入框在上，功能按钮在下）
        self.input_bar = InputBar(self.theme)
        layout.addWidget(self.input_bar)

        self.set_req(None)

    def set_req(self, req: Requirement | None):
        """切换当前需求：更新信息区 + 路由交互记录 + 注入 uploads 根目录。"""
        self._req = req
        self.info_panel.set_req(req)
        if req is None:
            self.chat_log.set_conversation(None)
            self.input_bar.reload_models()
            self.input_bar.set_uploads_root(None)
            return
        conv = req.active_conversation()
        self.chat_log.set_conversation(conv)
        # 模型下拉回显需求绑定模型
        self.input_bar.reload_models((req.provider, req.model_id))
        # 粘贴的附件落到需求目录 uploads/
        self.input_bar.set_uploads_root(req.root / "uploads")

    @property
    def req(self) -> Requirement | None:
        return self._req
