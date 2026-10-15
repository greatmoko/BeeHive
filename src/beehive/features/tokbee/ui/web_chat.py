"""TokBee 对话消息区：复用 WokBee 的网页聊天组件（QWebEngineView）。

替换旧的 QScrollArea + 每条消息一个原生部件方案（窗口缩放时整页重排卡顿）。
本模块只做数据转换：TokBee 消息 dict（role/content/reasoning_content/attachments）
→ WokBee `ProjectEvent`，渲染、流式打字、滚动全部由 _WebChat 前端承担。

- assistant 消息拆成两个事件：reasoning → 思考气泡（phase=reasoning），
  正文 → 回答气泡（phase=answer）；
- 用户消息 attachments 存入 meta，由前端渲染图片/文档 chip；
- 页面加载 `?clamp=full`：对话正文不截断（时间线页才需要 1 行折叠）。
"""
from __future__ import annotations

from PySide6.QtCore import QUrl

from beehive.features.wokbee.projects.models import ProjectEvent
from beehive.features.wokbee.ui.web_chat import _WebChat, _HTML_PATH

__all__ = ["TokBeeChatLog"]


def _msg_to_events(msg: dict) -> list[ProjectEvent]:
    """单条 TokBee 消息 → ProjectEvent 列表（assistant 可能拆两个事件）。"""
    role = str(msg.get("role") or "user")
    if role == "assistant":
        reasoning = str(msg.get("reasoning_content") or "")
        content = str(msg.get("content") or "")
        events: list[ProjectEvent] = []
        if reasoning.strip():
            events.append(ProjectEvent(kind="agent", content=reasoning, created_at="", meta={"phase": "reasoning"}))
        if content.strip() or not events:
            events.append(ProjectEvent(kind="agent", content=content, created_at="", meta={"phase": "answer"}))
        return events
    meta: dict = {}
    atts = msg.get("attachments")
    if isinstance(atts, list) and atts:
        meta["attachments"] = atts
    return [ProjectEvent(kind="user", content=str(msg.get("content") or ""), created_at="", meta=meta)]


def _msgs_to_events(msgs: list[dict]) -> list[ProjectEvent]:
    events: list[ProjectEvent] = []
    for msg in msgs or []:
        events.extend(_msg_to_events(msg))
    return events


class TokBeeChatLog(_WebChat):
    """TokBee 对话消息区：_WebChat 薄适配（页面不截断正文）。"""

    def _html_url(self) -> QUrl:
        url = QUrl.fromLocalFile(str(_HTML_PATH))
        url.setQuery("clamp=full")
        return url

    # ── 对外接口（与旧 ChatMessageArea 兼容的语义） ──────────────

    def set_messages(self, msgs: list[dict]):
        """整表渲染历史消息（TokBee 会话一次性加载，无需分页）。"""
        if not msgs:
            self.show_empty("开始和 AI 对话吧")
            return
        self.render_events(_msgs_to_events(msgs))

    def append_message(self, msg: dict):
        """追加一条消息（用户气泡 / assistant 定稿，含 reasoning 拆分）。"""
        for ev in _msg_to_events(msg):
            self.append_event(ev)

    def show_welcome(self, text: str = "选择一个对话或新建对话开始"):
        self.show_empty(text)
