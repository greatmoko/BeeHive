"""运行会话的 checkpoint、Agent 缓存与经验目录初始化。"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from wokbee.core.models import MAX_PROJECT_TITLE_LEN, Project
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


def ensure_experience_files(project_root: Path, project: Project) -> None:
    memory = memory_dir(project_root)
    memory.mkdir(parents=True, exist_ok=True)
    (memory / "experiences").mkdir(parents=True, exist_ok=True)
    content = (
        f"# Project {project.title}\n\n"
        f"- id: `{project.id}`\n"
        f"- goal: {project.goal or '(未设置)'}\n"
        f"- approval: {project.approval.summary()}\n\n"
        "你是 WokBee——运行在用户本机上的工作助手。能力范围、系统环境、可调用工具、"
        "目录与凭据约定见本轮系统提示与【会话上下文】。\n"
        "项目运行经验位于 memory/experiences/（只加载最新一份）。\n"
        "**禁止**访问 archives/；文件工具只用虚拟路径；凭据只给环境变量名，严禁写出账号密码。\n"
        f"项目名称最多 {MAX_PROJECT_TITLE_LEN} 字。\n"
    )
    (memory / "AGENTS.md").write_text(content, encoding="utf-8")
    from wokbee.engine.lessons import LessonStore

    LessonStore(project_root).rebuild_index()
