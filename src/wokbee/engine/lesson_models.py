"""经验数据模型与 Markdown 渲染。"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _stamp() -> str:
    # 含毫秒，避免同一秒内多次总结互相覆盖/排序错乱
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def _slug(text: str, max_len: int = 40) -> str:
    s = re.sub(r"\s+", "-", (text or "").strip())
    s = re.sub(r"[^\w\u4e00-\u9fff\-]+", "", s)
    s = s.strip("-")[:max_len] or "exp"
    return s.lower() if s.isascii() else s


@dataclass
class Lesson:
    """一条经验（Skills 风格 Markdown）；每次总结新建一份带时间戳文件。"""

    id: str = field(default_factory=lambda: f"exp_{uuid.uuid4().hex[:10]}")
    project_id: str = ""
    goal: str = ""
    outcome: str = "unknown"  # success | failed | cancelled | partial
    summary: str = ""  # 流程/方法摘要，非结果
    success_path: str = ""  # 实现步骤
    environment: str = ""
    notes: str = ""
    errors: str = ""
    model: str = ""
    policy: str = ""
    script_section: str = ""
    ai_section: str = ""
    order_section: str = ""
    scripts: list[str] = field(default_factory=list)
    pipeline: str = "scripts/pipeline.json"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    filename: str = ""  # 相对 memory/，如 experiences/exp_....md

    @property
    def description(self) -> str:
        base = (self.summary or self.goal or self.id).replace("\n", " ").strip()
        return f"[{self.outcome}] {base}"[:180]


def render_lesson_md(lesson: Lesson) -> str:
    """渲染经验文档：不含结果/产物章节。"""
    desc = lesson.description.replace('"', "'")
    scripts_yaml = ", ".join(f'"{s}"' for s in lesson.scripts) if lesson.scripts else ""
    display_name = lesson.filename or lesson.id
    lines = [
        "---",
        f"name: {_slug(lesson.goal or 'experience', 32)}",
        f"id: {lesson.id}",
        f'description: "{desc}"',
        f"outcome: {lesson.outcome}",
        f'goal: "{(lesson.goal or "").replace(chr(34), chr(39))[:120]}"',
        f"created_at: {lesson.created_at}",
        f"updated_at: {lesson.updated_at}",
        f"project_id: {lesson.project_id}",
        "automation: hybrid",
        f"pipeline: {lesson.pipeline or 'scripts/pipeline.json'}",
        f"file: {display_name}",
    ]
    if scripts_yaml:
        lines.append(f"scripts: [{scripts_yaml}]")
    lines.extend(
        [
            "---",
            "",
            f"# 项目经验：{lesson.goal or lesson.summary or lesson.id}",
            "",
            f"> 流程记录 · {lesson.outcome} · {lesson.created_at}",
            "",
            "> 本文件只记录**怎么做**（步骤/注意），不记录运行结果或交付产物；运行环境由系统每次自动注入。",
            "",
            "## 摘要（方法，非结果）",
            "",
            lesson.summary.strip() or "（无摘要）",
            "",
            "## 成功实现路径",
            "",
            "> 只保留**成功且必要**的有序步骤，后续运行将**严格照此执行**；"
            "每步按固定格式：`序号. 执行角色: \"{执行内容}\"; 【步骤说明】`——"
            "执行角色为 AI / 工具调用 / 脚本执行 / 系统执行；`{}` 内写明详细命令/脚本地址/提示词，"
            "`【】` 内为解释性说明；一律用虚拟路径，禁止 Windows 绝对路径。失败/试错/被弃用的尝试不写入。",
            "",
            (
                lesson.success_path.strip()
                or "（未记录具体步骤。）"
            ),
            "",
            "## 注意事项",
            "",
            "> 采用**无序列表**；每条 = **加粗的问题** + 解决办法（含具体规避做法/正确写法），"
            "后续运行遇到同类问题时照此处理。",
            "",
            lesson.notes.strip() or "（无特殊注意点）",
            "",
        ]
    )
    return "\n".join(lines)
