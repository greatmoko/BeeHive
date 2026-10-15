"""需求输入、草稿、附件和模型选择状态。"""
from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame
from beehive.common.ui.theme import Theme
from beehive.common.io.attachments import AttachmentState
from beehive.common.ui.attachments import refresh_attachment_bar
from beehive.features.dezibee.core.services import model_options
from beehive.features.dezibee.ui.input_layout import build_input_layout
from beehive.features.dezibee.ui.input_paste import handle_paste

class InputBar(QFrame):
    send_clicked = Signal(str, list)
    pause_clicked = Signal()
    open_folder_clicked = Signal()
    preview_clicked = Signal()
    export_clicked = Signal()
    new_conversation_clicked = Signal()
    model_changed = Signal(str, str)

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._running = False
        self._attachment_state = AttachmentState()
        self._attachments = self._attachment_state.items
        self._drafts: dict[str, str] = {}
        self._draft_req_id: str | None = None
        build_input_layout(self)
        self.reload_models()
        QTimer.singleShot(0, self._sync_input_resize_width)

    def _input_max_height(self) -> int:
        win = self.window()
        height = win.height() if win is not None else self.height()
        if height <= 0:
            height = 600
        return max(self._edit_min_h, int(height * 2 / 3))

    def _sync_input_resize_width(self):
        if self.width() > 0:
            self._input_resize.set_available_width(self.width())

    def _on_input_resize_delta(self, delta: int):
        if not delta:
            return
        max_height = self._input_max_height()
        new_height = min(
            max(self._edit.height() + delta, self._edit_min_h),
            max_height,
        )
        self._edit.setMaximumHeight(max_height)
        self._edit.setFixedHeight(new_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "_edit"):
            return
        self._sync_input_resize_width()
        max_height = self._input_max_height()
        self._edit.setMaximumHeight(max_height)
        self._edit.setFixedHeight(min(self._edit.height(), max_height))

    def set_uploads_root(self, root: str | Path | None):
        """由 view 在需求切换时注入：粘贴的图片/文件立即保存到 <需求目录>/uploads/。"""
        self._attachment_state.set_uploads_root(root)

    def set_draft_context(self, req_id: str | None):
        """切换需求时保存当前草稿，并加载目标需求自己的草稿。"""
        if self._draft_req_id is not None:
            self._drafts[self._draft_req_id] = self._edit.toPlainText()
        self._draft_req_id = req_id
        self._edit.blockSignals(True)
        self._edit.setPlainText(self._drafts.get(req_id, "") if req_id else "")
        self._edit.blockSignals(False)

    def _add_attachment(self, item: dict):
        if self._attachment_state.add(item):
            self._refresh_attach_bar()

    def _refresh_attach_bar(self):
        refresh_attachment_bar(self._attach_lay, self._attach_bar, self._attachments, self.theme, self._remove_attachment)

    def _remove_attachment(self, item: dict):
        self._attachment_state.remove(item)
        self._refresh_attach_bar()

    def _persist_inline(self, item: dict):
        """图片/文件（无本地 path）写盘到 uploads/，返回 path；失败则原样。"""
        return self._attachment_state.persist(item)

    def _take_attachments(self) -> list[dict]:
        """返回附件列表（供发送取走），随后清空 chip 条。"""
        items = self._attachment_state.take()
        self._refresh_attach_bar()
        return items

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

    def reload_models(self, selected: tuple[str, str] | None = None):
        """刷新模型列表；selected=(provider_id, model_id) 为当前使用模型。

        框上常显「切换模型」，不回显选中态；当前模型记录下来供
        悬停浮层展示、弹窗 ✓ 标记。
        """
        if selected is not None:
            self._current_model_pair = tuple(selected)
        self._model_combo.set_options(model_options(), self._current_model_pair)

    def _on_model_selected(self, provider_id: str, model_id: str):
        self._current_model_pair = (str(provider_id), str(model_id))
        self.model_changed.emit(provider_id, model_id)

    def _handle_paste(self):
        return handle_paste(self)
