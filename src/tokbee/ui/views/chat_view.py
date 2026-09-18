"""兼容门面：聊天视图实现位于 :mod:`chat_workspace`。"""

from .chat_workspace import (
    ChatView,
    _AiNameWorker,
    _AutoHeightBrowser,
    _ChatInputEdit,
    _ChatItem,
    _ChatSidebar,
    _ChatWorkspace,
    _CompactWorker,
    _InputResizeHandle,
    _UserBubbleLabel,
)

__all__ = [
    "ChatView",
    "_AiNameWorker",
    "_AutoHeightBrowser",
    "_ChatInputEdit",
    "_ChatItem",
    "_ChatSidebar",
    "_ChatWorkspace",
    "_CompactWorker",
    "_InputResizeHandle",
    "_UserBubbleLabel",
]
