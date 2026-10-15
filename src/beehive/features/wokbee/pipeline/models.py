"""按职责组织的 models。"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ScriptRunItem:
    path: str
    ok: bool
    output: str = ""
    error: str = ""
    description: str = ""
    step_id: str = ""


@dataclass
class PhaseResult:
    """单个有序步骤的执行结果。"""

    type: str  # script | ai
    ok: bool = True
    items: list[ScriptRunItem] = field(default_factory=list)
    ai_steps: list[dict] = field(default_factory=list)
    output: str = ""
    error: str = ""
    index: int = 0


@dataclass
class PipelineRunResult:
    """整条有序管线的状态（可能只跑到第一个脚本失败处）。"""

    ran: bool = False
    ok: bool = False
    items: list[ScriptRunItem] = field(default_factory=list)
    ai_steps: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    phases: list[dict] = field(default_factory=list)
    phase_results: list[PhaseResult] = field(default_factory=list)
    combined_output: str = ""
    error_summary: str = ""
    pipeline_path: Path | None = None
    need_ai: bool = True
    reason: str = ""
    # 交错执行：当前停在第几个 phase（0-based），后续由 runner 继续。
    # 每个 pipeline step 都是一个 phase，保留原始步骤边界，便于状态追踪。
    next_phase_index: int = 0
    # 仅保留脚本阶段产出；AI 阶段结果已在 checkpoint 消息历史中。
    context_parts: list[str] = field(default_factory=list)
