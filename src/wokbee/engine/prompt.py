"""【会话上下文】块构建；系统提示词从 sysprompt 兼容导出。"""

from __future__ import annotations

from sysprompt import static_system_prompt


def build_session_context_block(
    *,
    title: str,
    goal: str,
    approval_summary: str,
    max_steps: int | None = None,
    experience_digest: str = "",
    mode: str = "run",
    runtime_env_block: str = "",
    extra_lines: list[str] | None = None,
) -> str:
    """易变内容：拼进首条/当轮 user，不进 system。"""
    lines = [
        "【会话上下文】（本块可能随项目变更；勿写入对 system 稳定性的假设）",
        f"- 模式：{'交互' if mode == 'chat' else '设计' if mode == 'design' else '运行'}",
        f"- 项目名称：{title or '未命名项目'}",
        f"- 目标：{goal or '（未设置）'}",
        f"- 审核策略：{approval_summary or '（未设置）'}",
    ]
    if max_steps is not None and int(max_steps) > 0:
        lines.append(f"- 步数上限约：{int(max_steps)}（请聚焦目标）")
    if runtime_env_block.strip():
        lines.append("")
        lines.append(runtime_env_block.strip())
    if experience_digest.strip():
        lines.append("")
        lines.append(experience_digest.strip())
    if extra_lines:
        for line in extra_lines:
            s = (line or "").strip()
            if s:
                lines.append(s)
    return "\n".join(lines)


def compose_user_with_context(user_message: str, context_block: str) -> str:
    user_message = (user_message or "").strip()
    context_block = (context_block or "").strip()
    if not context_block:
        return user_message
    if not user_message:
        return context_block
    return f"{context_block}\n\n——\n{user_message}"
