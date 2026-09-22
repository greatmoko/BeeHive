"""运行会话的 checkpoint、Agent 缓存与经验目录初始化。"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from wokbee.core.paths import memory_dir


_checkpointers: dict[str, InMemorySaver] = {}
_agents: dict[str, Any] = {}
_lock = threading.Lock()


def get_checkpointer(project_id: str) -> InMemorySaver:
    with _lock:
        return _checkpointers.setdefault(project_id, InMemorySaver())


def reset_run_state(project_id: str) -> InMemorySaver:
    """新运行不继承上次中断或不完整的图状态。"""
    with _lock:
        _checkpointers[project_id] = InMemorySaver()
        _agents.pop(project_id, None)
        return _checkpointers[project_id]


def remember_agent(project_id: str, agent: Any) -> None:
    with _lock:
        _agents[project_id] = agent


def ensure_experience_files(project_root: Path) -> None:
    memory = memory_dir(project_root)
    memory.mkdir(parents=True, exist_ok=True)
    (memory / "experiences").mkdir(parents=True, exist_ok=True)
    from wokbee.engine.lessons import LessonStore

    LessonStore(project_root).rebuild_index()
