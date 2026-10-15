"""业务工具风险标签到通用中断解析器的适配。"""
from beehive.features.wokbee.execution.approval_policy import risk_label_for_tool
from beehive.system.agent.interrupt_state import _iter_interrupt_values, _first_ask_user_payload, _has_pending
from beehive.system.agent.interrupt_state import _pending_from_state as parse_pending

def _pending_from_state(agent, config):
    return parse_pending(agent, config, risk_label=risk_label_for_tool)
