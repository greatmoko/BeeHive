"""run/chat/design 的固定策略；执行、审批和 checkpoint 流程共用。"""

from dataclasses import dataclass
from hashlib import sha256


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

    def prepare_question(self, question, context, values, has_history):
        """返回本轮文本、checkpoint 元数据、需显示的上下文更新。"""
        if not self.design_workspace or not context:
            return question, {}, ""
        version = sha256(context.encode("utf-8")).hexdigest()
        messages = (values.get("messages") or []) if isinstance(values, dict) else []
        previous = None
        for message in reversed(messages):
            metadata = (
                message.get("additional_kwargs", {}) if isinstance(message, dict)
                else getattr(message, "additional_kwargs", {})
            )
            if "design_context" in metadata:
                previous = metadata["design_context"]
                break
        update = ""
        if not has_history:
            question = f"{context}\n\n【本轮指令】\n{question}"
        elif previous != version:
            # 旧记录或压缩后缺少版本标记时，显式同步当前版本。
            update = (
                "【需求上下文已更新】以下为当前版本，替代此前的需求上下文。\n"
                + context.replace("【需求上下文】\n", "", 1).replace(
                    "需求描述：", "需求描述已更新为：", 1
                )
            )
            question = f"{update}\n\n【本轮指令】\n{question}"
        return question, {"design_context": version}, update


_COMMON_TOOLS = "可用：联网 / 文件 / execute / Skills / MCP"
MODES = {
    "run": ModePolicy(
        "run", pipeline=True, experience=True, project_metadata=True, timeline=False, design_workspace=False,
        intro="运行模式（按经验管线执行）。", capabilities=_COMMON_TOOLS + " / 项目名称与目标工具。",
    ),
    "chat": ModePolicy(
        "chat", pipeline=False, experience=True, project_metadata=True, timeline=True, design_workspace=False,
        intro="交互模式（完整能力，不跑经验管线）。", capabilities=_COMMON_TOOLS + " / 项目名称与目标工具。",
    ),
    "design": ModePolicy(
        "design", pipeline=False, experience=False, project_metadata=False, timeline=False, design_workspace=True,
        intro="设计模式（DeziBee：不跑经验管线，不注入项目经验）。", capabilities=_COMMON_TOOLS + "。",
    ),
}


def mode_policy(mode: str) -> ModePolicy:
    try:
        return MODES[mode]
    except KeyError:
        raise ValueError(f"未知 Agent 模式：{mode}") from None
