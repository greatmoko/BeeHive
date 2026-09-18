"""兼容门面：项目工作区实现位于 :mod:`project_workspace`。"""

from .project_workspace import (
    STATUS_COLOR_KEY,
    STATUS_LABEL,
    TITLE_FROM_GOAL_LEN,
    _ProjectWorkspace,
)
from .project_metadata import _CompactWorker, _ProjectEssentials, _RefineMetaWorker
from .project_navigation import _ProjectItem, _ProjectSidebar

__all__ = [
    "STATUS_COLOR_KEY", "STATUS_LABEL", "TITLE_FROM_GOAL_LEN",
    "_CompactWorker", "_ProjectEssentials", "_ProjectItem", "_ProjectSidebar",
    "_ProjectWorkspace", "_RefineMetaWorker",
]
