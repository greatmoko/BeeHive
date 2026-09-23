"""WokBee 下段操作栏：输入、运行/发送（运行中再点同按钮即暂停）、审批条、模型切换、上下文用量环。"""

from __future__ import annotations

import random
import time
from pathlib import Path

from PySide6.QtCore import QBuffer, QEvent, QIODevice, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from tokbee.core.provider_store import ProviderStore
from tokbee.ui.combo_style import apply_combo_popup_style
from tokbee.ui.styles.system import bind_text_edit_context_menu
from tokbee.ui.styles.theme import Theme
from tokbee.ui.widgets.context_ring import ContextUsageRing
from ui_common.attachments import (
    AttachmentChip as _AttachmentChip,
    AttachmentState,
    IMAGE_EXTENSIONS as _IMAGE_EXTS,
    is_image_file as _is_image_file,
    open_attachment as _open_attachment,
    refresh_attachment_bar,
    sanitize_attachment_name as _sanitize_name,
)

# 运行中：活跃按钮（运行/发送）音频均衡器动效 —— 5 根细竖条自绘，底部对齐，随机跃升 + 指数回落
_RUN_EQ_BARS = 5
_RUN_EQ_BAR_W = 3        # 竖条宽度（细）
_RUN_EQ_GAP = 4          # 竖条间距
_RUN_EQ_PAD_TOP = 6
_RUN_EQ_PAD_BOTTOM = 6
_RUN_EQ_INTERVAL = 70    # 帧间隔（毫秒）
_RUN_EQ_DECAY = 0.82     # 每帧回落系数（真实均衡器风格）
_RUN_EQ_PEAK = 0.35      # 高于该值的随机量才触发跃升


class _InputResizeHandle(QWidget):
    """输入框顶部拖拽条：向上拖高、向下拖矮。"""

    drag_delta = Signal(int)

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self.setFixedSize(100, 8)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip("拖动调整高度")
        self._dragging = False
        self._last_y = 0

    def set_available_width(self, width: int):
        self.setFixedWidth(max(28, int(width) // 3))

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(self._theme.colors.get("border", "#e5e5e5")))
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        y = self.height() // 2
        painter.drawLine(8, y, self.width() - 8, y)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_y = event.globalPosition().toPoint().y()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            y = event.globalPosition().toPoint().y()
            delta = self._last_y - y
            self._last_y = y
            if delta:
                self.drag_delta.emit(delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _EqButton(QPushButton):
    """通用启停按钮：空闲显示原文字，运行中自绘均衡器动画并保持可点击（再点一次即暂停）。"""

    def __init__(self, theme: Theme, *, text: str, width: int, bg: str, bg_hover: str, parent=None):
        super().__init__(text, parent)
        self._theme = theme
        self._idle_text = text
        self._spinning = False
        self._levels = [0.0] * _RUN_EQ_BARS
        self._eq_timer = QTimer(self)
        self._eq_timer.setInterval(_RUN_EQ_INTERVAL)
        self._eq_timer.timeout.connect(self._tick)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(width, 34)
        c = theme.colors
        self.setStyleSheet(f"""
            QPushButton {{
                background: {bg}; color: white;
                border: none; border-radius: 6px; font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover {{ background: {bg_hover}; }}
            QPushButton:pressed {{ background: {bg}; }}
            QPushButton:disabled {{ background: {c['btn_bg']}; color: {c['text_hint']}; }}
        """)

    def set_spinning(self, spinning: bool):
        self._spinning = spinning
        if spinning:
            self._levels = [0.0] * _RUN_EQ_BARS
            self._eq_timer.start()
            self.setToolTip("点击暂停")
        else:
            self._eq_timer.stop()
            self.setText(self._idle_text)
            self.setToolTip("")
        self.update()

    def _tick(self):
        # 真实均衡器：随机跃升 + 指数回落
        for i in range(_RUN_EQ_BARS):
            peak = random.random()
            if peak > _RUN_EQ_PEAK:
                self._levels[i] = max(self._levels[i], peak)
            self._levels[i] *= _RUN_EQ_DECAY
        self.update()

    def paintEvent(self, event):
        if not self._spinning:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        painter.setBrush(QColor("#f5f5f5"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 6, 6)
        self._draw_equalizer(painter, rect)
        painter.end()

    def _draw_equalizer(self, painter: QPainter, rect):
        inner_h = (rect.height() - _RUN_EQ_PAD_TOP - _RUN_EQ_PAD_BOTTOM) * 0.8
        base_y = rect.y() + rect.height() - _RUN_EQ_PAD_BOTTOM
        total_w = _RUN_EQ_BARS * _RUN_EQ_BAR_W + (_RUN_EQ_BARS - 1) * _RUN_EQ_GAP
        start_x = rect.x() + (rect.width() - total_w) // 2
        lit = QColor("#07c160")
        dim = QColor("#f5f5f5")
        for i in range(_RUN_EQ_BARS):
            bar_h = round(self._levels[i] * inner_h)
            x = start_x + i * (_RUN_EQ_BAR_W + _RUN_EQ_GAP)
            painter.setBrush(dim)
            painter.drawRoundedRect(
                x, base_y - inner_h, _RUN_EQ_BAR_W, inner_h, 1, 1
            )
            if bar_h > 0:
                painter.setBrush(lit)
                painter.drawRoundedRect(
                    x, base_y - bar_h, _RUN_EQ_BAR_W, bar_h, 1, 1
                )

class _ActionBar(QFrame):
    run_clicked = Signal()
    pause_requested = Signal()
    open_folder_clicked = Signal()
    archive_clicked = Signal()
    upload_clicked = Signal()
    send_clicked = Signal(str, list)  # text, attachments
    approve_clicked = Signal()
    reject_clicked = Signal()
    model_changed = Signal(str, str)  # provider_id, model_id
    compress_clicked = Signal()
    draft_changed = Signal()

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._model_updating = False
        self._attachment_state = AttachmentState()
        self._attachments = self._attachment_state.items
        self._running_mode: str | None = None  # 运行中的模式：None | run | chat
        self._build()

    def _build(self):
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
                background: #fff7e6;
                border: 1px solid {c["warning"]};
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
        reject_btn.setFixedHeight(30)
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
        approve_btn.setFixedHeight(30)
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
        self._input.setStyleSheet(f"""
            QTextEdit {{
                background: {c["input_bg"]}; color: {c["text"]};
                border: 1px solid {c["input_border"]}; border-radius: 8px;
                padding: 8px; font-size: 13px;
            }}
            QTextEdit:focus {{ border: 1px solid {c["input_focus_border"]}; }}
        """)
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
            btn.setFixedSize(34, 34)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(self._icon_btn_qss())
            btn.clicked.connect(slot)
            row.addWidget(btn)

        row.addStretch()

        self._model_combo = QComboBox()
        self._model_combo.setFixedHeight(34)
        self._model_combo.setMinimumWidth(200)
        self._model_combo.setMaximumWidth(320)
        self._model_combo.setToolTip("切换本项目模型")
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
        self._model_combo.currentIndexChanged.connect(self._on_model_index_changed)
        row.addWidget(self._model_combo)

        self._ctx_ring = ContextUsageRing(self.theme)
        self._ctx_ring.setToolTip("上下文用量")
        self._ctx_ring.set_ring_enabled(False)
        row.addWidget(self._ctx_ring)

        self._run_btn = _EqButton(
            self.theme, text="运行", width=59, bg="#faad14", bg_hover="#e69500"
        )
        self._run_btn.clicked.connect(self._on_run_btn_clicked)
        row.addWidget(self._run_btn)

        self._send_btn = _EqButton(
            self.theme, text="发送", width=72, bg="#07c160", bg_hover="#06ad56"
        )
        self._send_btn.clicked.connect(self._on_send)
        row.addWidget(self._send_btn)
        layout.addSpacing(4)
        layout.addLayout(row)

        self.reload_models()
        QTimer.singleShot(0, self._sync_input_resize_width)

    def _input_max_height(self) -> int:
        win = self.window()
        height = win.height() if win is not None else self.height()
        if height <= 0:
            height = 600
        return max(self._input_min_h, int(height * 2 / 3))

    def _sync_input_resize_width(self):
        if self.width() > 0:
            self._input_resize.set_available_width(self.width())

    def _on_input_resize_delta(self, delta: int):
        if not delta:
            return
        max_height = self._input_max_height()
        new_height = min(
            max(self._input.height() + delta, self._input_min_h),
            max_height,
        )
        self._input.setMaximumHeight(max_height)
        self._input.setFixedHeight(new_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "_input"):
            return
        self._sync_input_resize_width()
        max_height = self._input_max_height()
        self._input.setMaximumHeight(max_height)
        self._input.setFixedHeight(min(self._input.height(), max_height))

    def _icon_btn_qss(self) -> str:
        c = self.theme.colors
        return f"""
            QPushButton {{
                background: {c["btn_bg"]}; color: {c["text"]};
                border: none; border-radius: 6px; font-size: 16px;
                padding: 0;
            }}
            QPushButton:hover {{ background: {c["btn_hover"]}; }}
            QPushButton:pressed {{ background: {c["accent_light"]}; }}
        """

    def eventFilter(self, obj, event):
        if obj is self._input:
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
                        return True  # Enter 发送，拦截默认换行
            elif event.type() == QEvent.Type.Clipboard:
                # 某些平台会在粘贴前发送 Clipboard 事件；只有识别到附件时才拦截。
                if self._handle_paste():
                    return True
        return super().eventFilter(obj, event)

    def _handle_paste(self):
        from PySide6.QtWidgets import QApplication

        cb = QApplication.clipboard()
        mime = cb.mimeData()
        got = False
        # 文件粘贴优先使用本地 URL，保留原始文件名（包括复制的图片文件）。
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
        # 纯截图/剪贴板图片没有原始文件名，只能生成唯一名称。
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
                            "display_name": f"pasted_image_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns() % 1_000_000:06d}.png",
                            "data": data,
                            "path": None,
                        }
                    )
                except Exception:
                    pass
                got = True
        if got and not self._input.toPlainText().strip():
            pass  # 保持输入框焦点即可
        return got

    def _add_attachment(self, item: dict):
        if self._attachment_state.add(item):
            self._refresh_attach_bar()

    def _refresh_attach_bar(self):
        refresh_attachment_bar(self._attach_lay, self._attach_bar, self._attachments, self.theme, self._remove_attachment)

    def _remove_attachment(self, item: dict):
        self._attachment_state.remove(item)
        self._refresh_attach_bar()

    def set_uploads_root(self, root: str | Path | None):
        """由 workspace 在项目切换时注入，粘贴带文件时立即保存到 uploads/。"""
        self._attachment_state.set_uploads_root(root)

    def _persist_inline(self, item: dict):
        """图片/文件（无本地 path）写盘到 uploads/，返回 path；失败则原样。"""
        return self._attachment_state.persist(item)

    def _take_attachments(self) -> list[dict]:
        """返回附件列表（供发送/运行取走），随后清空。"""
        items = self._attachment_state.take()
        self._refresh_attach_bar()
        return items

    def _on_run_btn_clicked(self):
        # 运行按钮即启停开关：run 模式运行中再点=暂停，否则启动运行。
        if self._running_mode == "run":
            self.pause_requested.emit()
            return
        self.run_clicked.emit()

    def _on_send(self):
        if self._running_mode:
            # chat 模式运行中点击发送=暂停；run 模式时按钮已禁用，此为 Enter 键兜底。
            if self._running_mode == "chat":
                self.pause_requested.emit()
            return
        text = self._input.toPlainText().strip()
        if text or self._attachments:
            self.send_clicked.emit(text, self._take_attachments())
            self._input.clear()

    def take_input(self, with_attachments: bool = False):
        text = self._input.toPlainText().strip()
        attachments = self._take_attachments() if with_attachments else []
        self._input.clear()
        if with_attachments:
            return text, attachments
        return text

    def set_draft(self, text: str):
        self._input.setPlainText(text or "")

    def current_attachments(self) -> list[dict]:
        """只读当前附件快照（供 settings 侧边栏等展示），不取出。"""
        return list(self._attachments)

    def set_running(self, running: bool, mode: str = "run"):
        """运行态切换：mode 决定哪个按钮跳动（run→运行按钮，chat→发送按钮），另一个按钮禁用。"""
        self._running_mode = mode if running else None
        run_active = running and mode == "run"
        chat_active = running and mode == "chat"
        self._run_btn.set_spinning(run_active)
        self._run_btn.setEnabled(not chat_active)
        self._send_btn.set_spinning(chat_active)
        self._send_btn.setEnabled(not run_active)
        self._model_combo.setEnabled(not running)

    def show_approval(self, text: str):
        self._approval_label.setText(text)
        self._approval_bar.setVisible(True)

    def hide_approval(self):
        self._approval_bar.setVisible(False)

    def reload_models(
        self,
        provider_id: str = "",
        model_id: str = "",
        *,
        fallback_provider: str = "",
        fallback_model: str = "",
    ):
        """刷新可选模型列表，并选中项目当前模型（或回退默认）。"""
        self._model_updating = True
        try:
            self._model_combo.clear()
            try:
                store = ProviderStore()
                models = store.list_selectable_models()
            except Exception:
                store = None
                models = []
            if not models:
                self._model_combo.addItem("未配置模型（请到厂商设置启用）", ("", ""))
                self._model_combo.setEnabled(False)
                return
            self._model_combo.setEnabled(True)
            target = (provider_id or "", model_id or "")
            # 回退顺序：厂商默认模型 → 调用方 fallback（WokBee 设置）→ 列表第一项
            if not target[1] and store is not None:
                try:
                    default = store.resolve_default()
                    if default:
                        target = (default.provider_id, default.model_id)
                except Exception:
                    pass
            if not target[1]:
                target = (fallback_provider or "", fallback_model or "")
            select = 0
            matched = False
            for i, m in enumerate(models):
                label = f"{m.provider_name} / {m.model_id}"
                self._model_combo.addItem(label, (m.provider_id, m.model_id))
                if (m.provider_id, m.model_id) == target and target[1]:
                    select = i
                    matched = True
            if not matched and not target[1]:
                select = 0
            self._model_combo.setCurrentIndex(select)
        finally:
            self._model_updating = False

    def set_context_usage(self, used: int, limit: int, *, enabled: bool = True):
        self._ctx_ring.set_usage(used, limit)
        self._ctx_ring.set_ring_enabled(False)

    def set_cache_stats(self, text: str = "", *, tooltip: str = ""):
        """缓存命中信息不再单独展示，合并进用量环的悬停提示。"""
        if text or tooltip:
            parts = [p for p in (text or "", tooltip or "") if p.strip()]
            self._ctx_ring.set_cache_info("\n".join(parts))
        else:
            self._ctx_ring.set_cache_info("")

    def draft_text(self) -> str:
        return self._input.toPlainText()

    def selected_model(self) -> tuple[str, str]:
        data = self._model_combo.currentData()
        if isinstance(data, tuple) and len(data) == 2:
            return str(data[0] or ""), str(data[1] or "")
        return "", ""

    def _on_model_index_changed(self, _index: int):
        if self._model_updating:
            return
        provider, model = self.selected_model()
        if model:
            self.model_changed.emit(provider, model)
