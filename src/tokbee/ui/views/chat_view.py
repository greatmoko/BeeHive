"""兼容门面：聊天视图实现位于 :mod:`chat_workspace`。"""
from .chat_workspace import *
from .chat_input import _ChatInputEdit, _InputResizeHandle
from .chat_messages import _AutoHeightBrowser, _UserBubbleLabel
from .chat_sidebar import _ChatItem, _ChatSidebar
