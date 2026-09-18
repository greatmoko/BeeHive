"""厂商添加、模型拉取和确认弹窗."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QComboBox, QWidget

from tokbee.ui.styles.theme import Theme
from tokbee.ui.styles.system import apply_combo_popup_style
from ui_common.dialogs import show_tip
from tokbee.core.provider_store import ProviderStore
from tokbee.core.errors import AIError

_CUSTOM_OPTION = "__custom_local__"
def _tip(parent: QWidget, theme: Theme, message: str):
    show_tip(parent, theme, message, height=150)


def _confirm(parent: QWidget, theme: Theme, message: str, title: str = "确认") -> bool:
    c = theme.colors
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setFixedSize(360, 150)
    dlg.setStyleSheet(f"background: {c['content_bg']};")
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 20, 24, 18)
    msg = QLabel(message)
    msg.setWordWrap(True)
    msg.setStyleSheet(f"font-size: 14px; color: {c['text']};")
    layout.addWidget(msg)
    layout.addStretch()
    row = QHBoxLayout()
    row.addStretch()
    cancel = QPushButton("取消")
    cancel.setFixedSize(72, 34)
    cancel.setCursor(Qt.CursorShape.PointingHandCursor)
    cancel.setStyleSheet(f"""
        QPushButton {{
            background: transparent; color: {c["text_secondary"]};
            border: 1px solid {c["border"]}; border-radius: 6px; font-size: 13px;
        }}
        QPushButton:hover {{ background: {c["subnav_hover"]}; }}
    """)
    cancel.clicked.connect(dlg.reject)
    row.addWidget(cancel)
    ok = QPushButton("删除")
    ok.setFixedSize(72, 34)
    ok.setCursor(Qt.CursorShape.PointingHandCursor)
    ok.setStyleSheet(f"""
        QPushButton {{
            background: {c["btn_primary"]}; color: #ffffff;
            border: none; border-radius: 6px; font-size: 13px;
        }}
        QPushButton:hover {{ background: {c["btn_primary_hover"]}; }}
    """)
    ok.clicked.connect(dlg.accept)
    row.addWidget(ok)
    layout.addLayout(row)
    return dlg.exec() == QDialog.DialogCode.Accepted

class _FetchModelsWorker(QThread):
    finished_ok = Signal(list)
    finished_err = Signal(str)

    def __init__(self, host: str, key: str, parent=None):
        super().__init__(parent)
        self._host = host
        self._key = key

    def run(self):
        try:
            models = ProviderStore.fetch_remote_models(self._host, self._key)
            self.finished_ok.emit(models)
        except AIError as e:
            self.finished_err.emit(str(e))
        except Exception as e:
            self.finished_err.emit(str(e))


class _AddProviderDialog(QDialog):
    """选择内置厂商，或填写名称添加自定义本地 API。"""

    def __init__(self, theme: Theme, store: ProviderStore, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.store = store
        self.result_builtin_id: str | None = None
        self.result_custom: tuple[str, str, str] | None = None  # name, host, key
        self._build()

    def _build(self):
        c = self.theme.colors
        self.setWindowTitle("添加厂商")
        self.setFixedSize(440, 420)
        self.setStyleSheet(f"background: {c['content_bg']};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(10)

        lbl_style = f"font-size: 13px; font-weight: 600; color: {c['text']};"
        hint = f"font-size: 11px; color: {c['text_hint']};"
        inp = f"""
            QLineEdit, QComboBox {{
                background: {c["input_bg"]}; border: 1px solid {c["input_border"]};
                border-radius: 6px; padding: 0 10px; color: {c["text"]}; font-size: 13px;
                min-height: 34px;
            }}
            QLineEdit:focus, QComboBox:focus {{ border-color: {c["input_focus_border"]}; }}
            QComboBox QAbstractItemView {{
                background: {c["content_bg"]};
                border: 1px solid {c["input_border"]};
                outline: none;
                selection-background-color: {c["btn_hover"]};
                selection-color: {c["text"]};
            }}
            QComboBox QAbstractItemView::item {{
                padding: 6px 10px; min-height: 26px;
                border: none; outline: none; color: {c["text"]};
            }}
            QComboBox QAbstractItemView::item:hover,
            QComboBox QAbstractItemView::item:selected,
            QComboBox QAbstractItemView::item:focus {{
                background: {c["btn_hover"]};
                border: none; outline: none;
            }}
        """

        t = QLabel("选择要添加的厂商")
        t.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {c['text']};")
        layout.addWidget(t)

        tip = QLabel("内置厂商与「自定义本地 API」分开；多个本地服务请分别添加并填写厂商名称。")
        tip.setWordWrap(True)
        tip.setStyleSheet(hint)
        layout.addWidget(tip)

        type_lbl = QLabel("厂商类型")
        type_lbl.setStyleSheet(lbl_style)
        layout.addWidget(type_lbl)

        self._type_combo = QComboBox()
        self._type_combo.setStyleSheet(inp)
        from tokbee.ui.combo_style import apply_combo_popup_style
        apply_combo_popup_style(self._type_combo, c)
        for pid, name, icon, _f in self.store.list_addable_builtins():
            self._type_combo.addItem(f"{icon}  {name}", pid)
        self._type_combo.addItem("🖥️  自定义本地 API", _CUSTOM_OPTION)
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        layout.addWidget(self._type_combo)

        self._custom_box = QWidget()
        custom_l = QVBoxLayout(self._custom_box)
        custom_l.setContentsMargins(0, 8, 0, 0)
        custom_l.setSpacing(8)

        name_lbl = QLabel("厂商名称（必填）")
        name_lbl.setStyleSheet(lbl_style)
        custom_l.addWidget(name_lbl)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("例如：公司内网 vLLM / LM Studio-办公机")
        self._name_edit.setStyleSheet(inp)
        custom_l.addWidget(self._name_edit)

        host_lbl = QLabel("API Host")
        host_lbl.setStyleSheet(lbl_style)
        custom_l.addWidget(host_lbl)
        self._host_edit = QLineEdit()
        self._host_edit.setPlaceholderText("http://127.0.0.1:1234/v1")
        self._host_edit.setStyleSheet(inp)
        custom_l.addWidget(self._host_edit)

        key_lbl = QLabel("API Key（可选）")
        key_lbl.setStyleSheet(lbl_style)
        custom_l.addWidget(key_lbl)
        self._key_edit = QLineEdit()
        self._key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_edit.setPlaceholderText("本地服务通常可留空")
        self._key_edit.setStyleSheet(inp)
        custom_l.addWidget(self._key_edit)

        layout.addWidget(self._custom_box)
        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("取消")
        cancel.setFixedSize(72, 34)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {c["text_secondary"]};
                border: 1px solid {c["border"]}; border-radius: 6px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {c["subnav_hover"]}; }}
        """)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)

        ok = QPushButton("添加")
        ok.setFixedSize(72, 34)
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_primary"]}; color: #ffffff;
                border: none; border-radius: 6px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {c["btn_primary_hover"]}; }}
        """)
        ok.clicked.connect(self._on_ok)
        btn_row.addWidget(ok)
        layout.addLayout(btn_row)

        self._on_type_changed()

    def _on_type_changed(self):
        is_custom = self._type_combo.currentData() == _CUSTOM_OPTION
        self._custom_box.setVisible(is_custom)
        self.setFixedSize(440, 420 if is_custom else 260)

    def _on_ok(self):
        pid = self._type_combo.currentData()
        if pid == _CUSTOM_OPTION:
            name = self._name_edit.text().strip()
            if not name:
                self._name_edit.setFocus()
                _tip(self, self.theme, "请填写厂商名称")
                return
            self.result_custom = (
                name,
                self._host_edit.text().strip(),
                self._key_edit.text().strip(),
            )
            self.accept()
            return
        if not pid:
            _tip(self, self.theme, "没有可添加的内置厂商")
            return
        self.result_builtin_id = str(pid)
        self.accept()



__all__ = ["_tip", "_confirm", "_FetchModelsWorker", "_AddProviderDialog"]

