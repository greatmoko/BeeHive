"""run/chat/design 的固定策略；执行、审批和 checkpoint 流程共用。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModePolicy:
    name: str
    pipeline: bool
    experience: bool
    project_metadata: bool
    timeline: bool
    design_workspace: bool
    intro: str
    capabilities: str



_COMMON_TOOLS = "可用：联网 / 文件 / execute / Skills / MCP"
_MEMORY_TOOLS = "三级记忆：全局记忆加载及受限更新建议 / 原子记忆按需查询与写入 / 会话记忆选择性读取与自动写入"
MODES = {
    "run": ModePolicy(
        "run", pipeline=True, experience=True, project_metadata=True, timeline=False, design_workspace=False,
        intro="运行模式（按经验管线执行）。", capabilities=_COMMON_TOOLS + " / 项目名称与目标工具。",
    ),
    "chat": ModePolicy(
        "chat", pipeline=False, experience=True, project_metadata=True, timeline=True, design_workspace=False,
        intro="交互模式（完整能力，不跑经验管线）。", capabilities=_COMMON_TOOLS + " / 项目名称与目标工具 / " + _MEMORY_TOOLS + "。",
    ),
    "design": ModePolicy(
        "design", pipeline=False, experience=False, project_metadata=False, timeline=False, design_workspace=True,
        intro="设计模式（DeziBee：不跑经验管线，不注入项目经验）。", capabilities=_COMMON_TOOLS + " / " + _MEMORY_TOOLS + "。",
    ),
}


def mode_policy(mode: str) -> ModePolicy:
    try:
        return MODES[mode]
    except KeyError:
        raise ValueError(f"未知 Agent 模式：{mode}") from None


def mode_label(mode: str, *, first_run: bool = True) -> str:
    mode_policy(mode)
    if mode == "run":
        return "wokbee/run/first" if first_run else "wokbee/run/repeat"
    return "dezibee/design" if mode == "design" else "wokbee/chat"
