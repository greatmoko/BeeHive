"""按职责组织的 editor。"""
from __future__ import annotations
import json
import shlex
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QDialog, QComboBox, QTextEdit
from beehive.common.ui.theme import Theme
from beehive.system.mcp.mcp_store import McpServerConfig


class _McpEditor(QDialog):
    def __init__(self, theme: Theme, server: McpServerConfig | None = None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._server = server or McpServerConfig(name="新 MCP")
        self.setWindowTitle("编辑 MCP" if server else "添加 MCP")
        self.setFixedSize(520, 540)
        c = theme.colors
        self.setStyleSheet(f"background: {c['content_bg']};")
        from beehive.common.ui.styles.selectors import apply_combo_popup_style
        from beehive.common.ui.styles.inputs import apply_textedit, rounded_lineedit_qss
        from beehive.common.ui.styles.buttons import apply_primary_btn, secondary_btn_qss
        from beehive.common.ui.styles.metrics import DEFAULT_COMBO_WIDTH
        from beehive.common.ui.styles.metrics import DEFAULT_COMBO_HEIGHT
        inp = rounded_lineedit_qss(c)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(8)

        name_lbl = QLabel("名称")
        name_lbl.setStyleSheet(f"font-size: 13px; color: {c['text']}; background: transparent; border: none;")
        lay.addWidget(name_lbl)
        self._name = QLineEdit(self._server.name)
        self._name.setFixedHeight(34)
        self._name.setStyleSheet(inp)
        lay.addWidget(self._name)

        transport_lbl = QLabel("传输方式")
        transport_lbl.setStyleSheet(f"font-size: 13px; color: {c['text']}; background: transparent; border: none;")
        lay.addWidget(transport_lbl)
        self._transport = QComboBox()
        self._transport.addItem("stdio（本地进程）", "stdio")
        self._transport.addItem("SSE", "sse")
        self._transport.addItem("Streamable HTTP", "streamable_http")
        idx = self._transport.findData(self._server.transport)
        self._transport.setCurrentIndex(idx if idx >= 0 else 0)
        self._transport.currentIndexChanged.connect(self._sync_fields)
        apply_combo_popup_style(
            self._transport, c, rounded=True,
            fixed_width=DEFAULT_COMBO_WIDTH, fixed_height=DEFAULT_COMBO_HEIGHT,
        )
        lay.addWidget(self._transport, alignment=Qt.AlignmentFlag.AlignLeft)

        self._cmd_label = QLabel("命令（如 npx / uvx / python）")
        self._cmd_label.setStyleSheet(f"font-size: 13px; color: {c['text']}; background: transparent; border: none;")
        lay.addWidget(self._cmd_label)
        self._command = QLineEdit(self._server.command)
        self._command.setFixedHeight(34)
        self._command.setStyleSheet(inp)
        lay.addWidget(self._command)

        self._args_label = QLabel("参数（空格分隔）")
        self._args_label.setStyleSheet(f"font-size: 13px; color: {c['text']}; background: transparent; border: none;")
        lay.addWidget(self._args_label)
        self._args = QLineEdit(shlex.join(self._server.args) if self._server.args else "")
        self._args.setFixedHeight(34)
        self._args.setStyleSheet(inp)
        lay.addWidget(self._args)

        self._url_label = QLabel("URL（SSE / HTTP）")
        self._url_label.setStyleSheet(f"font-size: 13px; color: {c['text']}; background: transparent; border: none;")
        lay.addWidget(self._url_label)
        self._url = QLineEdit(self._server.url)
        self._url.setFixedHeight(34)
        self._url.setStyleSheet(inp)
        lay.addWidget(self._url)

        env_lbl = QLabel("环境变量 JSON（可选，如 {\"KEY\":\"val\"}）")
        env_lbl.setStyleSheet(f"font-size: 13px; color: {c['text']}; background: transparent; border: none;")
        lay.addWidget(env_lbl)
        self._env = QTextEdit()
        self._env.setFixedHeight(80)
        self._env.setPlainText(json.dumps(self._server.env, ensure_ascii=False) if self._server.env else "")
        apply_textedit(self._env, c)
        lay.addWidget(self._env)

        self._sync_fields()

        lay.addSpacing(12)
        lay.addStretch(1)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addStretch()
        cancel = QPushButton("取消")
        cancel.setFixedHeight(34)
        cancel.setMinimumWidth(72)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setAutoDefault(False)
        cancel.setDefault(False)
        cancel.setFlat(False)
        cancel.setStyleSheet(secondary_btn_qss(c))
        cancel.clicked.connect(self.reject)
        ok = QPushButton("保存")
        apply_primary_btn(ok, c)
        ok.setMinimumWidth(72)
        ok.setAutoDefault(False)
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

    def _sync_fields(self):
        t = self._transport.currentData()
        is_stdio = t == "stdio"
        for w in (self._cmd_label, self._command, self._args_label, self._args):
            w.setVisible(is_stdio)
        for w in (self._url_label, self._url):
            w.setVisible(not is_stdio)

    def result_server(self) -> McpServerConfig | None:
        name = self._name.text().strip()
        if not name:
            return None
        t = self._transport.currentData() or "stdio"
        env = {}
        raw_env = self._env.toPlainText().strip()
        if raw_env:
            try:
                parsed = json.loads(raw_env)
                if isinstance(parsed, dict):
                    env = {str(k): str(v) for k, v in parsed.items()}
            except json.JSONDecodeError:
                return None
        args_text = self._args.text().strip()
        args = shlex.split(args_text, posix=False) if args_text else []
        return McpServerConfig(
            id=self._server.id,
            name=name,
            enabled=self._server.enabled,
            transport=str(t),
            command=self._command.text().strip(),
            args=args,
            url=self._url.text().strip(),
            env=env,
            cwd=self._server.cwd,
        )
