"""WokBee 下段操作栏：输入、运行/发送（运行中再点同按钮即暂停）、审批条、模型切换、上下文用量环。"""

from __future__ import annotations
from beehive.features.wokbee.ui.action_layout import build_action_bar
from beehive.features.wokbee.ui.action_paste import handle_paste

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame

from beehive.system.providers.provider_store import ProviderStore
from beehive.common.ui.theme import Theme
from beehive.common.io.attachments import AttachmentState
from beehive.common.ui.attachments import refresh_attachment_bar


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
        self._attachment_state = AttachmentState()
        self._attachments = self._attachment_state.items
        self._running_mode: str | None = None  # 运行中的模式：None | run | chat
        self._build()

    def _build(self):
        return build_action_bar(self)

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
        return handle_paste(self)

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
        try:
            store = ProviderStore()
            models = store.list_selectable_models()
        except Exception:
            store = None
            models = []
        options = [
            (f"{m.provider_name} / {m.model_id}", (m.provider_id, m.model_id))
            for m in models
        ]
        if not options:
            self._model_combo.set_placeholder("未配置模型（请到厂商设置启用）")
            self._model_combo.set_options([], ("", ""))
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
        if target[1] and not any(pair == target for _, pair in options):
            target = ("", "")  # 目标模型已不在可选列表，回退第一项（与旧下拉行为一致）
        if not target[1]:
            target = options[0][1]
        self._model_combo.set_options(options, target)

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
        return self._model_combo.current_model()
