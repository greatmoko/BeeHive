"""经验写入所需的日志读取和确定性回退文本。"""

from __future__ import annotations

import logging
from pathlib import Path
from beehive.features.wokbee.projects.paths import events_path
from beehive.features.wokbee.projects.round_events import load_events_file
from beehive.features.wokbee.experience.lesson_events import slice_latest_round

logger = logging.getLogger("wokbee")


def load_latest_round_events(project_root: Path) -> list:
    """读取最新一轮事件；损坏行和读取失败不影响经验落盘。"""
    try:
        events = load_events_file(events_path(project_root))
        return slice_latest_round(events)[-400:]
    except OSError:
        logger.exception("读取运行日志失败，AI 总结将缺少日志上下文")
        return []


def fallback_success_path(outcome: str, errors: str, summary: str) -> str:
    if outcome != "success":
        return (
            "本次未形成完整成功路径。建议下次：\n"
            "- 先读最新 memory/exp_*.md\n"
            "- 优先复用已验证数据源、本地脚本与工具顺序\n"
            f"- 关注失败原因：{errors or summary}"
        )
    return (
        "1. AI:\"{提示词: 明确目标与约束，制定执行方案}\";[AI 环节：理解任务并规划]\n"
        "2. 工具调用:\"{cmd: 联网获取或读取 uploads/ 获取真实数据}\";[获取真实数据，禁止用 archives/；同名或相近文件以最新修改时间为准]\n"
        "3. 工具调用:\"{cmd: 在项目根目录起草临时文件，最终交付写入 output/}\";[起草并交付]\n"
        "4. AI:\"{提示词: 用中文说明过程与数据来源}\";[AI 环节：交代过程与数据来源（经验中不记录结果正文）]"
    )


def fallback_notes(errors: str) -> str:
    notes = []
    if errors:
        notes.append(
            f"- **本次运行报错**：复跑前先核对环境与脚本——{errors[:300]}；"
            "可固化步骤已固化到 memory/（仅供参考），下次由 AI 判断是否执行。"
        )
    notes.extend((
        "- **需要实时数据**：必须联网获取，禁止凭记忆编造；禁止访问 archives/ 归档数据。",
        "- **脚本 callback 需留痕**：脚本执行后把 callback 写入项目根目录 script_callback_*.md，AI 环节先读再写，禁止编造。",
    ))
    return "\n".join(notes)
