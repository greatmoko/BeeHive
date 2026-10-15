"""可固化脚本步骤与固化结果。"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
@dataclass
class ScriptStep:
    tool: str
    args: dict[str, Any]
    description: str
    rel_path: str = ""

@dataclass
class SolidifyResult:
    script_steps: list[ScriptStep] = field(default_factory=list)
    pipeline_rel: str = "memory/pipeline.json"
    script_section_md: str = ""
    order_section_md: str = ""
