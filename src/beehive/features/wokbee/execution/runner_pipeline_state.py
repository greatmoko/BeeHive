"""单次项目运行执行的显式状态。"""
from dataclasses import dataclass, field
from typing import Any
@dataclass
class PipelineState:
    agent: Any = None
    config: dict = field(default_factory=dict)
    seen_msg_ids: set = field(default_factory=set)
    base_message: str = ""
    base_content: Any = ""
    final_text: str = ""
    trajectory_messages: list = field(default_factory=list)
