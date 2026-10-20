"""【会话上下文】块构建；系统提示词从 sysprompt 兼容导出。"""

from __future__ import annotations

from beehive.system.prompts import static_system_prompt
from beehive.features.wokbee.execution.runner_modes import mode_label


def build_session_context_block(
    *,
    title: str,
    goal: str,
    approval_summary: str,
    max_steps: int | None = None,
    experience_digest: str = "",
    mode: str = "run",
    first_run: bool = True,
    project_id: str = "",
    project_root: str = "",
    runtime_env_block: str = "",
    extra_lines: list[str] | None = None,
) -> str:
    """易变内容：拼进首条/当轮 user，不进 system。"""
    lines = [
        "【会话上下文】（本块可能随项目变更；勿写入对 system 稳定性的假设）",
        f"【当前模式】{mode_label(mode, first_run=first_run)}",
        f"- 项目名称：{title or '未命名项目'}",
        f"- 目标：{goal or '（未设置）'}",
        f"- 审核策略：{approval_summary or '（未设置）'}",
    ]
    if max_steps is not None and int(max_steps) > 0:
        lines.append(f"- 步数上限约：{int(max_steps)}（请聚焦目标）")
    if project_root:
        root = str(project_root)
        dir_table = "\n".join(
            [
                "  - archives/：归档快照——禁止访问，归档数据禁止读取；",
                "  - uploads/：用户上传文件——可读写引用，长期保留；",
                "  - output/：项目交付物——最终成果必须写在这里；",
                "  - memory/：应用维护的记忆（events.jsonl 事件日志、exp_时间戳.md 经验、"
                "可复用脚本、技能快照）——可读写；"
                "events.jsonl 与已有经验由系统维护，不要主动改写；",
                "  - .project/：系统维护（project.json 项目配置 + Agent.md 用户自定义项目规则）"
                "——Agent 默认不可改写；",
                "  - 项目根目录：临时脚本、参考文件、中间材料、执行反馈 script_callback_*.md "
                "以及运行产生的文档都放这里，无需主动整理归档（归档时统一清理）；"
                "可复用脚本经确认后固化到 memory/。",
            ]
        )
        lines.extend(
            [
                "",
                "【工作目录与目录说明】",
                f"- 项目 ID：{project_id or '（未知）'}",
                f"- 项目根目录（绝对路径）：{root}",
                "- 虚拟路径：文件工具的 / 即项目根（如 /read_file /foo.py → 项目根\\foo.py）；",
                f"- execute 工具：用真实路径，工作目录（cwd）为项目根 {root}；",
                "- 路径映射示例：/read_file /foo.py → 项目根\\foo.py；"
                "execute .\\foo.py（cwd=项目根）。",
                "- 目录作用：",
                dir_table,
            ]
        )
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
