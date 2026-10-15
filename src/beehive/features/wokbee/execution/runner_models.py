"""业务运行请求、结果与通用步骤预算导出。"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from beehive.system.providers.provider_models import ResolvedModel
from beehive.features.wokbee.projects.models import ApprovalFlags, Project
from beehive.system.agent.step_budget import StepBudget, StepLimitExceeded

@dataclass
class RunRequest:
    project: Project
    project_root: Path
    user_message: str
    resolved: ResolvedModel
    approval: ApprovalFlags
    max_steps: int = 128
    attachments: list[dict] = field(default_factory=list)
    runner_mode: str = ""
    chat_thread_id: str = ""
    design_context: str = ""

@dataclass
class RunResult:
    ok: bool
    outcome: str
    final_text: str = ""
    error: str = ""
    lesson_id: str = ""
    pending_actions: list[dict] = field(default_factory=list)
