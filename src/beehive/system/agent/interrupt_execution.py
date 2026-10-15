"""澄清与审批中断恢复，业务结果由调用方适配。"""
from langgraph.types import Command
from beehive.system.agent.interrupt_state import _has_pending, _first_ask_user_payload

def resume_interrupts(runtime, agent, config, seen_msg_ids, *, stream, parse_pending, wait_approval, wait_ask_user, emit):
    """处理 ask_user / 工具审批中断，直到无 pending 或需外部等待。"""
    guard = 0
    while _has_pending(agent, config) and guard < 50:
        guard += 1
        if runtime._cancel.is_set():
            return dict(
                ok=False,
                outcome="cancelled",
                error="已取消",
            )

        ask_payload = _first_ask_user_payload(agent, config)
        if ask_payload:
            n = len(ask_payload.get("questions") or [])
            emit(
                "info",
                f"AI 需要你澄清意图（{n} 题），请在弹窗中作答…",
                {"ask_user": ask_payload},
            )
            answers = wait_ask_user(ask_payload)
            if runtime._cancel.is_set():
                return dict(
                    ok=False,
                    outcome="cancelled",
                    error="已取消",
                )
            if answers.get("cancelled"):
                emit("info", "你取消了澄清提问。")
            else:
                emit("info", "已收到你的澄清回答，继续执行…")
            stream(
                agent,
                Command(resume=answers),
                config,
                seen_msg_ids,
            )
            continue

        pending = parse_pending(agent, config)
        if not pending:
            break

        lines = []
        for i, act in enumerate(pending, 1):
            lines.append(
                f"{i}. [{act.get('risk')}] {act.get('name')}: {act.get('description')}"
            )
        emit(
            "approval",
            "需要审批以下操作：\n" + "\n".join(lines),
            {"pending": pending},
        )

        decisions = wait_approval(pending)
        if runtime._cancel.is_set():
            return dict(
                ok=False,
                outcome="cancelled",
                error="已取消",
            )

        approved = sum(1 for d in decisions if d.get("type") == "approve")
        rejected = len(decisions) - approved
        emit(
            "approval",
            f"审批结果：通过 {approved}，拒绝 {rejected}",
            {"decisions": decisions},
        )

        if runtime._approval_timed_out:
            return dict(ok=False, outcome="failed", error="审批等待超时，已拒绝并结束本轮")

        stream(
            agent,
            Command(resume={"decisions": decisions}),
            config,
            seen_msg_ids,
        )
    return None
