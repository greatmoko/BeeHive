from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from wokbee.engine.lesson_models import Lesson
from wokbee.engine.lesson_pipeline import (
    build_success_path_from_pipeline,
    sync_lesson_from_pipeline,
)

_AI_SUMMARY_SYSTEM = """你是 WokBee 的「经验总结」助手。根据「上一份经验 + 本次运行日志 + 现有脚本」总结可复用的流程经验。
不要输出实现过程、压缩轨迹、关键线索或问题与解决方案章节。

⚠️ 首要提醒（最重要）：你总结的**经验文档 / pipeline.json 步骤 / 脚本**，在后续项目运行时会被**严格照章执行**——
错误、含糊、不严谨的路径（脚本地址、命令、数据源）将直接导致后续运行失败，代价远高于本次修正。因此必须：
- 只总结**真实验证过、有效、可复用**的内容，且**尽量简短**；剔除一切失败、试错、被弃用或重复的尝试。
- 每条路径、脚本名、命令都必须来自本次运行日志，**禁止编造**；脚本路径须与项目下真实文件一致。
- 用户已上传到 uploads/ 的脚本必须直接运行原文件；禁止 Copy-Item 到项目根目录，禁止再次包装已有 scripts/ 脚本。
- 一律使用虚拟路径（scripts/、workspace/、deliverables/、uploads/、memory/…），**禁止 Windows 绝对路径**（如 C:\\Users\\…）。

硬性要求：
1. 经验只包含：**摘要**、**成功实现路径**、**注意事项**；其中成功实现路径必须与 pipeline.json 对齐。
2. 禁止写入：最终结果数值、交付产物内容、报告正文、截图描述、成功产出的具体文案；不记录运行环境（系统每次自动注入）。
3. 不要引用或依赖 archives/ 归档数据。
4. **success_path（成功实现路径）必须与 pipeline_steps 一一对应**：
   - pipeline_steps 是机器执行顺序的唯一事实来源；success_path 只能解释这些步骤，不能另行增加目录查看、文件搜索、验证或 Agent 内部思考步骤。
   - 最终落盘时系统会再次从实际写入的 pipeline 生成 success_path，因此不要编造 pipeline 之外的路径。
   - 在 pipeline_steps 尚未确定前，只保留真正成功且必要的有序步骤，步骤尽量精简：
   - 执行顺序直接体现在编号中（脚本步骤 ↔ AI 环节按真实顺序排），不再单列「执行顺序/可本地脚本步骤/需 AI 完成的步骤」章节。
   - 若后续步骤依赖前置结果（需要前置数据/确认才能选对输入），应按**逻辑依赖**顺序排，勿把历史里「先取数、后补前置确认」的脏顺序原样固化。例如「先用 Get-Date 确认当前日期，再选取对应日期的数据」「先读配置，再跑脚本」。
    - 剔除所有失败调用、试错、被弃用/未采用的方案；保留每个真实业务步骤的边界，
      不强制合并相邻的同类型步骤，因为同一脚本的重复执行可能是有意的验证步骤。
   - 每步必须按**固定格式**书写：
     `序号. 执行角色: "{执行内容}"; 【步骤说明】`
     - **序号**：从 1 开始递增。
     - **执行角色**：AI / 工具调用 / 脚本执行 / 系统执行 等——AI 判断加工标 `AI`，文件/联网等工具标 `工具调用`，本地脚本步骤标 `脚本执行`（自动执行，AI 不介入），系统自动过程标 `系统执行`。
     - **执行内容**：用 `{}` 包起来，写明**详细且明确**的执行命令 / 提示词 / 脚本名称与路径（脚本步骤可直接引用 `uploads/` 用户脚本，或引用 `scripts/` 下真实脚本）。
     - **步骤说明**：用 `【】` 包起来，是对执行内容的解释性描述（做什么 × 达成什么目的）。
     - 示例：
       `1. 工具调用: "{cmd: 读取 workspace/script_callback_*.md}"; 【查看上一步脚本回调，确认产物完整】`
       `2. 脚本执行: "{cmd: execute scripts/query_weather.bat}"; 【运行天气查询脚本（自动执行），原始数据落 workspace/】`
       `3. AI: "{提示词: 依据 callback 数据提炼要点并成文，写入 deliverables/}"; 【AI 环节：成文交付】`
5. 自动化脚本与管线约定（重要）：
   - 可复用本地命令落到项目 `scripts/`；运行输出落到 `workspace/script_callback_*.md`。
   - **只在本次运行日志中确实出现过、且尚未固化的可复用命令才写 script_files**（.py/.bat/.cmd/.ps1/.json/.sh/.js/.vbs）。
      禁止凭空发明「预检/校验/回读」等日志里没有的脚本；同一脚本只有在真实路径确需
      重复验证时才重复写入步骤。
    - **pipeline_steps 必须基于本次运行日志中实际执行过的步骤，按真实时间顺序逐步列出**；每一步判定类型：
     - `script`：能够确定性、机械、重复执行的工作（API 请求、文件处理、数据转换、发布复制等）→
       用户上传脚本直接引用 `uploads/...`，其他新脚本才固化为 `scripts/...`，后续直接执行、不耗 Token；
     - `ai`：必须依赖 AI 的理解/分析/整理/创意/写作/判断的工作（如“根据收集的材料撰写报告”）→
       固化为**明确的业务任务**（description + prompt_hint），后续运行仍调用 LLM 执行该固定任务，
       但**禁止重新规划整个 Pipeline**；
     **禁止保存 Agent 内部思考过程**（读取 Skill、思考下一步、决定调用什么工具、自由探索等）——
     那不是业务任务；ai 步骤必须是一句明确的业务任务，例如“根据前面收集的数据生成报告”。
     **禁止凭空新增日志中没有的步骤/脚本**；**禁止根据 AI 自己的理解增加目标里没有的任务**
     （如目标只要求「运行脚本并把产物放到 deliverables」，就不要再加检查/总结/校验步骤）。
     简单任务（如：执行用户脚本 → 发布产物到 deliverables）通常只需 2~3 步；
     复杂任务按实际执行可以有更多步骤。
   - **交付约定**：若目标要求把产物放到 deliverables/，pipeline_steps 必须包含一个
     「发布」脚本步骤——把脚本**实际产出文件**复制/移动到 deliverables/（保留原始文件与
     文件名，**不要合并成 final.md 代替原始产物**）；脚本步骤 path 指向项目内真实文件。
    - **pipeline_steps** 决定下次「运行」的真实顺序：按数组从头到尾逐步执行——script 步骤自动跑
     （不耗 Token）；ai 步骤调用 LLM 执行已确定的业务任务（按需消耗 Token）。
       **所有 script path 必须是项目相对虚拟路径**（仅允许 `scripts/...` 或 `uploads/...`，禁止 Windows 绝对路径）；提交前先用文件工具确认文件存在且可访问，写入后系统会重新读取 pipeline 并用同一规则复核，失败则拒绝保存。
       **不生成 final_ai**，也不在管线结尾强制再唤一次 AI；如果“总结/写报告/生成内容”本身就是
     用户 Goal 的一部分，它应作为正常的 `ai` 步骤固化在管线中。
6. **注意事项（notes）写作规范**：
   - 采用**无序列表**（每项以 `-` 开头），不要按「问题1/处理1」编号排序。
   - 每条 = **问题加粗** + 解决办法（含具体规避做法或正确写法），同一条目内给出。
   - 格式：`- **问题简述**：解决办法（具体做法）。`
   - 示例：
     - **查询文件不存在**：以虚拟路径访问与校验（workspace/、scripts/…），勿使用 Windows 绝对路径。
     - **脚本 callback 缺失**：脚本执行后把输出写入 workspace/script_callback_*.md，AI 环节先读再写，禁止编造。
7. 用中文。输出必须是一个 JSON 对象（不要 Markdown 围栏），字段如下：
{
  "summary": "摘要：概要介绍经验的主要作用（一两段，非结果）",
  "success_path": "仅成功且必要的有序步骤（按固定格式：序号. 执行角色: \"{执行内容}\"; 【步骤说明】，每步=操作+目的，执行顺序直接体现在编号中）",
  "notes": "注意事项：无序列表（- 开头），每条 = **加粗问题** + 解决办法（具体做法）",
  "used_skills": ["skill-folder-name"],
  "reference_materials": [
    {"path": "uploads/references/config.json", "note": "服务端环境参数，复跑需用"}
  ],
  "script_files": [
    {"filename": "query_weather.bat", "content": "@echo off\\n...", "description": "...", "in_pipeline": true}
  ],
  "pipeline_steps": [
    {"type": "script", "path": "scripts/collect_data.py", "description": "API 请求并保存原始数据到 workspace/（自动执行）"},
    {"type": "ai", "description": "根据 workspace/ 中的收集材料整理分析并撰写报告", "prompt_hint": "先读 workspace/script_callback_*.md，再写报告到 deliverables/"},
    {"type": "script", "path": "scripts/publish_deliverables.py", "description": "发布：把脚本实际产出文件复制到 deliverables/（保留原始文件）"}
  ]
}
说明：
- **script 步骤**：确定性/机械工作，后续自动执行（不耗 Token）；
  **ai 步骤**：明确的业务任务（理解/分析/整理/创意/写作/判断），后续运行照样调 LLM 执行该固定任务，
  但**禁止重新规划整个 Pipeline**。不要把「读取 Skill / 思考下一步 / 决定调用什么工具」保存为
  ai 步骤；如果“总结/写报告/生成内容”本身就是用户 Goal 的一部分，它应作为正常的 `ai` 步骤固化，
  而不是结尾的 final_ai。不生成 final_ai，也不在管线结尾强制再唤一次 AI。
- **严格对齐目标与实际执行**：不要根据 AI 自己的理解增加目标里没有的任务（如检查、总结、校验）；
  目标要求什么就交付什么。若目标要求交付文件到 deliverables/，必须含一个把**实际产出文件**
  复制到 deliverables/ 的发布步骤（不得用 final.md 合并代替原始文件）。
- **script_files**：脚本最终会按规范重命名为 `项目ID_脚本作用(≤4词)_时间戳.扩展名`（filename 仅作作用提示）；
  内容必须完整可独立运行，description 写清作用。**绝不覆盖/删除 scripts/ 下已有脚本**。
- **used_skills**：本次真实调用过的全局 Skill 目录名（如 "web-search"、"pdf-tools"），供快照到 uploads/references/skills/。
- **reference_materials**：本次用到的可复用外部材料（第三方代码/登录与密钥配置/环境参数等），需保存进 uploads/references/ 并登记；敏感信息仅供本机使用。没有则为空数组。
- 有可复用命令时尽量同时给出 script_files 与 pipeline_steps；没有则可为空数组。
"""


def _extract_json_object(text: str) -> str | None:
    """从可能带围栏/前后叙述的文本里截出第一个平衡的 {…} JSON 对象。"""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _parse_ai_summary_json(text: str) -> dict | None:
    """宽松解析 AI 总结 JSON：去围栏 → 直接 → 截首个平衡 {…} → 失败返回 None。"""
    t = (text or "").strip()
    if not t:
        return None
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    try:
        data = json.loads(t)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    obj = _extract_json_object(t)
    if obj is not None:
        try:
            data = json.loads(obj)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def summarize_lesson_with_ai(
    *,
    model: Any,
    goal: str,
    outcome: str,
    previous_experience: str,
    run_log: str,
    scripts_context: str,
    environment_hint: str = "",
    phase_states: str = "",
) -> dict[str, Any]:
    """调用模型总结经验；失败时抛出异常由调用方回退。

    返回字段均为 str，另含：
    - script_files: list[dict]（AI 手写脚本，可为空）
    - pipeline_steps: list[dict]（有序管线步骤）
    - used_skills: list[str]（本次用到的 Skill 目录名）
    - reference_materials: list[dict]（需保存进 uploads/references/ 的材料）
    生成过程在内部收齐，结束后一次性返回，避免向时间线刷进度气泡。
    """
    user = (
        f"项目目标：{goal or '（未设置）'}\n"
        f"本轮 outcome：{outcome}\n\n"
        f"## 上一份经验（可能为空）\n{previous_experience or '（无）'}\n\n"
        f"## 本次运行日志\n{run_log}\n\n"
        f"## 本轮阶段状态（按时间顺序；失败时必须据此修正管线）\n"
        f"{phase_states or '（没有预定义 pipeline 阶段；请从运行日志还原真实步骤）'}\n\n"
        f"## 现有脚本与 pipeline\n{scripts_context}\n\n"
        f"## 环境提示\n{environment_hint or '（无）'}\n\n"
        "请输出符合要求的 JSON。你总结的经验/管线/脚本在后续运行时会被**严格照章执行**，"
        "错误或不严谨的路径会直接破坏后续工作：务必只写真实验证过、可复用且**尽量短**的步骤；"
        "路径一律用虚拟路径（scripts/、workspace/、deliverables/、uploads/、memory/…），禁止 Windows 绝对路径。"
        "success_path 只保留**成功且必要**的有序步骤，每步按固定格式："
        "`序号. 执行角色:\"{执行内容}\";[步骤说明]`（执行角色=AI/工具调用/脚本执行/系统执行；"
        "`{}` 内写详细命令/脚本地址/提示词，`[]` 内写解释性说明），执行顺序直接体现在编号中，"
        "剔除失败/试错/被弃用尝试，但不要强制合并相邻的同类型步骤；"
        "notes 用无序列表，每条=**加粗问题**+解决办法。"
        "若日志里出现可复用"
        "的本地脚本/命令（如 execute 跑 .py/.bat、Skill 脚本），请在 script_files 写出完整源码，"
        "并在 pipeline_steps 明确给出每次执行的脚本地址/命令。"
        "pipeline_steps 以日志中真实跑过的步骤为准，严格保持实际顺序和步骤边界，禁止凭空新增；每步判定类型："
        "`script`=确定性/机械工作（API 请求、文件处理、数据转换、发布复制等，后续自动执行不耗 Token）；"
        "`ai`=必须依赖 AI 的理解/分析/整理/创意/写作/判断的业务任务（如“根据收集的材料撰写报告”，"
        "后续运行照样调 LLM 执行该固定任务，但**禁止重新规划整个 Pipeline**）。"
        "**禁止把 Agent 内部思考过程保存为 ai 步骤**（读取 Skill、思考下一步、决定调用什么工具、"
        "自由探索等）；ai 步骤必须是一句明确的业务任务。"
        "**严格对齐项目目标与实际执行**：不要根据 AI 自己的理解增加目标里没有的任务"
        "（如目标只要求运行脚本并把产物放到 deliverables，就不要再加检查/总结/校验步骤）。"
        "不生成 final_ai，也不在管线结尾强制再唤一次 AI；如果“总结/写报告/生成内容”本身就是"
        "用户 Goal 的一部分，它应作为正常的 ai 步骤固化在管线中。"
        "若目标要求交付文件到 deliverables/，pipeline_steps 必须包含一个发布步骤："
        "把脚本**实际产出文件**复制/移动到 deliverables/（保留原始文件，不要合并成 final.md 代替产物）。"
        "简单任务（执行→发布）通常 2~3 步。"
        "请逐一判断阶段状态：成功、失败-AI接管后成功、失败-AI接管后失败。"
        "只要存在失败阶段，就必须直接产出修正后的 pipeline_steps/脚本方案，"
        "让下一轮按修正版执行并更新项目经验；不要只描述失败而不修正。"
        "若用到了第三方代码/登录/环境参数，"
        "请填到 used_skills 与 reference_materials，供保存到 uploads/references/ 供下次稳定复跑。"
    )
    messages = [
        {"role": "system", "content": _AI_SUMMARY_SYSTEM},
        {"role": "user", "content": user},
    ]
    text = ""
    # 优先 stream 仅用于内部拼装；不向外刷进度。失败则 invoke。
    try:
        parts: list[str] = []
        for chunk in model.stream(messages):
            piece = getattr(chunk, "content", None)
            if piece is None:
                continue
            if isinstance(piece, list):
                piece = "".join(
                    b.get("text", "") if isinstance(b, dict) else str(b) for b in piece
                )
            piece = str(piece)
            if piece:
                parts.append(piece)
        text = "".join(parts).strip()
    except Exception:
        text = ""
    if not text:
        resp = model.invoke(messages)
        raw = getattr(resp, "content", None) or str(resp)
        if isinstance(raw, list):
            raw = "\n".join(
                b.get("text", "") if isinstance(b, dict) else str(b) for b in raw
            )
        text = str(raw).strip()

    data = _parse_ai_summary_json(text)
    if data is None:
        raise ValueError("AI 总结未返回有效 JSON（已按宽松解析尝试，仍失败）")
    out: dict[str, Any] = {}
    for key in (
        "summary",
        "success_path",
        "notes",
    ):
        val = data.get(key)
        out[key] = str(val).strip() if val is not None else ""
    out["script_files"] = _normalize_ai_script_files(data.get("script_files"))
    out["pipeline_steps"] = _normalize_ai_pipeline_steps(data.get("pipeline_steps"))
    out["used_skills"] = _normalize_ai_used_skills(data.get("used_skills"))
    out["reference_materials"] = _normalize_ai_reference_materials(
        data.get("reference_materials")
    )
    return out


_AI_SUMMARY_JUDGE_SYSTEM = """你是 WokBee 的「经验与 Pipeline 是否需要更新」决策助手。

背景：项目已有至少一份经验与 scripts/pipeline.json（后续运行按 steps 顺序执行）。你需要根据
「最新经验 + 本次运行日志 + 本轮结果」判断**是否值得**新建一份更新后的经验、并重写 pipeline.json。

本次运行若没有出现任何异常（脚本全部成功、输出正常），默认不更新。

需要判断的三种情形（结合本次异常及最终成功解决方案）：
1. **偶发错误**（网络抖动、临时超时、外部服务暂不可用等）：本次偶发，不修改 Pipeline，也不改经验。
2. **原 Pipeline 本身的问题**（脚本/顺序/命令有误或过时导致失败，AI 已用新方法修正）：需要更新
   经验并重写 Pipeline（以本次真实成功执行的操作/脚本/顺序为准）。
3. **发现了更稳定、更好的执行路径**（新脚本、更快的顺序、更可靠的命令，且已真实验证成功）：
   应该替换原 Pipeline 与经验。

不必更新的情形：
1. 完全按已有经验+脚本稳定复跑成功，无新错误、无新方法、执行顺序未变（含偶发错误且已由
   现有路径稳定恢复）。
2. 仅结果/数据变化（经验不记录结果），流程/方法/环境层面无新信息。
3. 运行被用户取消，无实质新信息。

硬性要求：
- 只返回一个 JSON 对象（不要 Markdown 围栏），格式：
  {"should_update": true 或 false, "reason": "一句话理由，中文"}
- should_update 默认应偏向 false（省 token，且避免每次都覆盖 Pipeline）；只有确有意义的新方法 /
  新错误修正 / 新顺序（即情形 2、3）时才为 true。
"""


def judge_should_update_experience(
    *,
    model: Any,
    goal: str,
    outcome: str,
    previous_experience: str,
    run_log: str,
) -> tuple[bool, str]:
    """用轻量模型调用判断本次运行是否值得更新经验。失败一律返回 (False, "")。"""
    user = (
        f"项目目标：{goal or '（未设置）'}\n"
        f"本轮 outcome：{outcome}\n\n"
        f"## 最新一份经验（仅流程方法，不含结果）\n{previous_experience or '（无）'}\n\n"
        f"## 本次运行日志\n{run_log or '（无）'}\n\n"
        "请判断是否需要更新经验，仅返回 JSON。"
    )
    messages = [
        {"role": "system", "content": _AI_SUMMARY_JUDGE_SYSTEM},
        {"role": "user", "content": user},
    ]
    text = ""
    try:
        parts: list[str] = []
        for chunk in model.stream(messages):
            piece = getattr(chunk, "content", None)
            if piece is None:
                continue
            if isinstance(piece, list):
                piece = "".join(
                    b.get("text", "") if isinstance(b, dict) else str(b) for b in piece
                )
            piece = str(piece)
            if piece:
                parts.append(piece)
        text = "".join(parts).strip()
    except Exception:
        text = ""
    if not text:
        try:
            resp = model.invoke(messages)
            raw = getattr(resp, "content", None) or str(resp)
            if isinstance(raw, list):
                raw = "\n".join(
                    b.get("text", "") if isinstance(b, dict) else str(b) for b in raw
                )
            text = str(raw).strip()
        except Exception:
            return False, ""
    data = _parse_ai_summary_json(text)
    if not isinstance(data, dict):
        return False, ""
    return bool(data.get("should_update")), str(data.get("reason") or "").strip()



def _normalize_ai_script_files(raw: Any) -> list[dict[str, Any]]:
    """校验并规整 AI 返回的 script_files。"""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        filename = str(
            item.get("filename") or item.get("name") or item.get("path") or ""
        ).strip()
        content = item.get("content")
        if content is None:
            content = item.get("source") or item.get("code") or ""
        content = str(content)
        if not filename or not content.strip():
            continue
        if len(content) > 256_000:
            content = content[:256_000]
        desc = str(item.get("description") or item.get("desc") or "").strip()
        in_pipeline = item.get("in_pipeline")
        if in_pipeline is None:
            in_pipeline = item.get("pipeline", True)
        out.append(
            {
                "filename": filename,
                "content": content,
                "description": desc,
                "in_pipeline": bool(in_pipeline),
            }
        )
    return out


def _normalize_ai_pipeline_steps(raw: Any) -> list[dict[str, Any]]:
    """规整 AI 给出的有序管线步骤。

    不做硬性数量限制（复杂任务步骤可以多、AI 环节可以多）：
    仅去掉**完全重复**的步骤（同一 type+path+description+args+prompt_hint），
    避免明显的重复浪费；同一脚本在不同阶段跑（参数/说明不同）会保留。
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw[:80]):
        if not isinstance(item, dict):
            continue
        t = str(item.get("type") or "").lower().strip()
        if t not in ("script", "ai"):
            continue
        step: dict[str, Any] = {
            "id": str(item.get("id") or f"{t}_{i+1}"),
            "type": t,
            "description": str(item.get("description") or "").strip()[:300],
        }
        if t == "script":
            path = str(item.get("path") or item.get("filename") or "").strip().replace("\\", "/")
            if path and not path.startswith("scripts/"):
                path = f"scripts/{Path(path).name}"
            if not path:
                continue
            step["path"] = path
            step["tool"] = str(item.get("tool") or "ai_authored")
            step["args"] = item.get("args") if isinstance(item.get("args"), dict) else {}
        else:
            step["prompt_hint"] = str(item.get("prompt_hint") or item.get("hint") or "").strip()
            if not step["description"]:
                step["description"] = "AI 步骤"
        out.append(step)
    # 不去重：同一脚本在不同步骤重复执行可能是经过验证的成功路径。
    return out


def _dedupe_identical_pipeline_steps(steps: list[dict]) -> list[dict]:
    """去掉完全重复的步骤（type+path+description+args+prompt_hint 全同）。"""
    if not steps:
        return steps
    seen: set[str] = set()
    out: list[dict] = []
    for s in steps:
        key = json.dumps(
            {
                "t": s.get("type"),
                "p": str(s.get("path") or ""),
                "d": str(s.get("description") or "").strip(),
                "a": s.get("args") if isinstance(s.get("args"), dict) else {},
                "h": str(s.get("prompt_hint") or "").strip(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _normalize_ai_used_skills(raw: Any) -> list[str]:
    """规整 AI 返回的 used_skills（Skill 目录名列表，去重保序）。"""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw[:50]:
        if isinstance(item, str):
            s = item.strip()
        elif isinstance(item, dict):
            s = str(item.get("name") or "").strip()
        else:
            s = ""
        if s and s not in out:
            out.append(s)
    return out


def _normalize_ai_reference_materials(raw: Any) -> list[dict[str, Any]]:
    """规整 AI 返回的 reference_materials（path + note）。"""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:50]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        note = str(item.get("note") or item.get("desc") or "").strip()[:300]
        if not path and not note:
            continue
        out.append({"path": path, "note": note})
    return out
