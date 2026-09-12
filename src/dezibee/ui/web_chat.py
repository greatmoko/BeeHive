"""DeziBee 聊天记录窗口：复用 WokBee 的网页聊天组件（QWebEngineView）。

把 DeziBee 的 Conversation 事件（dict 形式的 ProjectEvent）转换为 ProjectEvent
后交给 WokBee `_WebChat` 渲染：Markdown 气泡、思考卡片、工具 call/callback
配对行、流式打字与定稿去重全部由 chat.html 前端完成，替换旧的
QScrollArea + AutoHeightMd 方案（长对话下渲染卡顿、布局变形）。
"""

from __future__ import annotations

import logging
import uuid

from tokbee.ui.styles.theme import Theme

from wokbee.core.models import ProjectEvent
from wokbee.ui.web_chat import _WebChat

logger = logging.getLogger("dezibee")


def _conv_event_to_project_event(ev: dict) -> ProjectEvent:
    """Conversation.events 里的 dict → ProjectEvent（补全工具事件的 meta）。

    旧数据可能缺 meta（tool 事件无 tool/tool_call_id），这里按内容前缀
    兜底解析 `**call:**` / `**callback:**` 格式，保证网页端 call/callback
    能配对成一条工具行。
    """
    kind = str(ev.get("kind") or "info")
    content = str(ev.get("content") or "")
    meta = ev.get("meta") if isinstance(ev.get("meta"), dict) else {}

    if kind == "tool" and not meta:
        tool = ""
        phase = ""
        if content.startswith("**call:**") or content.startswith("**callback:**"):
            first_line = content.splitlines()[0]
            phase = "call" if first_line.startswith("**call:**") else "callback"
            # 形如 **call:** `ls`
            inside = first_line.split("`", 1)
            tool = inside[1].split("`", 1)[0] if len(inside) > 1 else "tool"
        ev = ProjectEvent(
            id=uuid.uuid4().hex[:10],
            kind="tool",
            content=content,
            meta={
                "tool": tool or "tool",
                "phase": phase or "callback",
                "tool_call_id": f"legacy_{uuid.uuid4().hex[:8]}",
                "status": "success",
            },
        )
    else:
        ev = ProjectEvent.from_dict(
            {
                "id": str(ev.get("id") or uuid.uuid4().hex[:10]),
                "kind": kind,
                "content": content,
                "created_at": str(ev.get("created_at") or ""),
                "meta": meta,
            }
        )
    return ev


class DeziBeeChatLog(_WebChat):
    """WokBee 网页聊天窗口的 DeziBee 适配：事件源是 Conversation.events。"""

    def __init__(self, theme: Theme, parent=None):
        super().__init__(theme, parent)

    # ── 与旧 ChatLogPanel 兼容的接口 ─────────────────────

    def set_conversation(self, conversation) -> None:
        """切换对话并全量渲染（conversation 为 dezibee Conversation 或 None）。"""
        if conversation is None or not getattr(conversation, "events", None):
            self.render_events([])
            return
        events = [
            _conv_event_to_project_event(ev)
            for ev in conversation.events
            if isinstance(ev, dict)
        ]
        # 过滤纯流式占位（agent_stream 不落盘，历史里不会有；保险起见跳过空内容）
        events = [e for e in events if (e.content or "").strip()]
        self.render_events(events)

    def _render_event(self, ev: dict) -> None:
        """追加一条事件（旧 ChatLogPanel 接口名，供 view 增量推送）。"""
        self.append_event(_conv_event_to_project_event(ev))

    def _append_bubble(self, role: str, content: str) -> None:
        """追加一条简单消息（role: user/ai/tool/error/info）。"""
        kind = {"user": "user", "ai": "agent", "tool": "tool", "error": "error"}.get(
            role, "info"
        )
        self.append_event(
            ProjectEvent(
                id=uuid.uuid4().hex[:10],
                kind=kind,
                content=content,
                meta={},
            )
        )

    def begin_run(self) -> None:  # noqa: F811 - 覆盖基类同名方法语义一致
        super().begin_run()

    def end_stream(self) -> None:
        """整轮结束：等价于基类 end_run（收尾流式气泡/未完成工具行）。"""
        self.end_run()

    # append_stream / finalize_stream：基类已提供 append_stream(target, delta)；
    # finalize_stream 语义（用完整事件定稿去重）由网页端 onAppendEvent 内部完成，
    # 视图侧只需在完整 agent 事件到达时照常 append_event 即可。
    def finalize_stream(self, target: str, content: str) -> bool:
        return False  # 网页端自动去重：完整事件直接 append_event，不需要手动定稿
