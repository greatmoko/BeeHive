"""DeziBee 上下文总结：材料拼装 + 轻量单轮模型调用（不跑 Agent 管线）。"""

from __future__ import annotations

import logging
import re
from dezibee.core.models import Conversation, Requirement

_SUMMARY_INSTRUCTION = (
    "你是一名产品设计上下文整理助手。请把以下「需求 + 历史交互 + 当前 Demo/PRD 状态 + 设计决策」"
    "整理成一个结构化的上下文摘要（Markdown），供后续设计对话沿用。\n\n"
    "输出严格使用以下结构（不要省略小节，若无内容填「（暂无）」）：\n"
    "# Design Context\n\n"
    "## 产品目标\n\n...\n\n"
    "## 当前页面\n\n...\n\n"
    "## 已确定设计\n\n...\n\n"
    "## 已实现功能\n\n...\n\n"
    "## 页面结构\n\n...\n\n"
    "## 业务逻辑\n\n...\n\n"
    "## 当前 Demo 状态\n\n...\n\n"
    "## 后续待处理\n\n...\n\n"
    "要求：只依据给定材料总结，不要臆造；保持简要；保留关键设计决策与待办。"
)
logger = logging.getLogger("dezibee")


def build_summary_material(
    req: Requirement,
    conversation: Conversation | None = None,
    *,
    max_chars: int = 30000,
) -> str:
    """拼装总结材料：需求 + 交互记录 + 各对话摘要 + Demo/PRD 状态。"""
    parts: list[str] = []
    parts.append(f"# 需求\n名称：{req.title}\nID：{req.id}\n描述：{req.description or '（无）'}")
    parts.append(f"状态：{req.status}；创建：{req.created_at}；更新：{req.updated_at}")

    # 交互记录（当前对话）
    if conversation is not None:
        lines = []
        for ev in conversation.events:
            kind = (ev.get("kind") or "")
            content = (ev.get("content") or "").strip()
            if not content:
                continue
            if kind == "user":
                lines.append(f"用户：{content[:800]}")
            elif kind == "agent":
                lines.append(f"AI：{content[:1500]}")
            elif kind == "error":
                lines.append(f"错误：{content[:500]}")
        if lines:
            parts.append("# 交互记录\n" + "\n".join(lines[-120:]))
        else:
            parts.append("# 交互记录\n（暂无）")
    else:
        parts.append("# 交互记录\n（暂无）")

    # 其他对话的摘要（保留决策）
    others = [c for c in req.conversations if c.conv_id != (conversation.conv_id if conversation else None)]
    other_summaries = [c.summary for c in others if c.summary.strip()]
    if other_summaries:
        parts.append("# 其他对话摘要\n" + "\n\n".join(other_summaries[-5:]))

    # Demo/PRD 状态
    parts.append("# 当前 Demo 状态\n" + _demo_status(req))
    parts.append("# 当前 PRD\n" + _prd_status(req))

    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…（摘要材料已截断）"
    return text


def _demo_status(req: Requirement) -> str:
    demo_dir = req.demo_dir
    if not demo_dir.exists():
        return "（尚未生成）"
    files: list[str] = []
    for p in sorted(demo_dir.rglob("*")):
        if p.is_file():
            try:
                files.append(str(p.relative_to(req.root)))
            except ValueError:
                continue
    if not files:
        return "（目录为空）"
    listing = "\n".join(f"- {f}" for f in files[-60:])
    # 摘取 index.html 的开头元信息，便于判断当前页面主题
    idx = demo_dir / "index.html"
    snippet = ""
    if idx.exists():
        try:
            head = idx.read_text(encoding="utf-8", errors="replace")[:2000]
            title = re.search(r"<title[^>]*>(.*?)</title>", head, re.S | re.I)
            snippet = f"\n（index.html <title>：{title.group(1).strip() if title else '无'}）"
        except OSError:
            pass
    return f"文件清单：\n{listing}{snippet}"


def _prd_status(req: Requirement) -> str:
    prd_dir = req.prd_dir
    if not prd_dir.exists():
        return "（尚未生成）"
    files = sorted(p for p in prd_dir.rglob("*.md") if p.is_file())
    if not files:
        return "（目录为空）"
    listing = "\n".join(f"- {p.relative_to(req.root)}" for p in files[-40:])
    return f"PRD 文件清单：\n{listing}"


def perform_summarize(material: str, model=None) -> str:
    """轻量单轮模型调用生成摘要。

    复用 AIClient（tokbee.core.ai_client，urllib 直连，无 deepagents 依赖）
    或 build_chat_model（ChatOpenAI）调用；两者失败时用启发式抽取兜底。
    """
    if model is not None:
        return _summarize_with_chatopenai(model, material)
    # 默认用轻量 AIClient（fallback 到 ChatOpenAI）
    try:
        return _summarize_with_ailight(material)
    except Exception as e:
        logger.warning("AIClient 摘要失败，回退 ChatOpenAI：%s", e)
        try:
            resolved = _resolve_first_model()
            if resolved is None:
                raise RuntimeError("无可用的 AI 模型")
            from wokbee.engine.model_factory import build_chat_model

            return _summarize_with_chatopenai(build_chat_model(resolved), material)
        except Exception as e2:
            logger.warning("ChatOpenAI 摘要失败，使用启发式摘要：%s", e2)
            return _heuristic_summarize(material)


def _resolve_first_model():
    from tokbee.core.provider_store import ProviderStore

    store = ProviderStore()
    try:
        return store.first_resolved()
    except Exception:
        return None


def _summarize_with_ailight(material: str) -> str:
    """用 AIClient 轻量调用一次（非流式）。"""
    from tokbee.core.ai_client import AIClient

    store = _default_store()
    resolved = store.first_resolved()
    if resolved is None:
        raise RuntimeError("未配置可用的 AI 模型")
    client = AIClient(
        endpoint=resolved.api_host,
        api_key=resolved.api_key,
        model=resolved.model_id,
        family=resolved.family,
        protocol=resolved.api_protocol or "chat",
    )
    messages = [
        {"role": "system", "content": _SUMMARY_INSTRUCTION},
        {"role": "user", "content": material},
    ]
    resp = client.chat(messages)
    text = getattr(resp, "content", None) or getattr(resp, "text", "") or ""
    if not isinstance(text, str):
        text = str(text)
    return _sanitize_summary(text)


def _default_store():
    from tokbee.core.provider_store import ProviderStore

    return ProviderStore()


def _summarize_with_chatopenai(model, material: str) -> str:
    messages = [
        {"role": "system", "content": _SUMMARY_INSTRUCTION},
        {"role": "user", "content": material},
    ]
    resp = model.invoke(messages)
    text = getattr(resp, "content", None)
    if isinstance(text, list):
        text = "".join(
            getattr(part, "text", None) or str(part) if not isinstance(part, str) else part
            for part in text
        )
    if not isinstance(text, str):
        text = str(text)
    return _sanitize_summary(text)


def _sanitize_summary(summary: str) -> str:
    summary = (summary or "").strip()
    if not summary:
        return "（无摘要输出）"
    return summary[:12000]


def _heuristic_summarize(material: str) -> str:
    """无模型可用时的兜底：抽取需求 + 交互记录关键行。"""
    lines = [ln for ln in material.splitlines() if ln.strip()]
    out: list[str] = ["# Design Context", "", "## 产品目标"]
    title = _first_after(lines, "# 需求")
    if title:
        out.append(title)
    out += ["", "## 交互记录（关键行）"]
    key = [
        ln[:200] for ln in lines
        if ln.startswith(("用户：", "AI："))
    ][-40:]
    out.extend(key or ["（无）"])
    out += ["", "## 当前 Demo 状态"]
    out.append(_first_after(lines, "# 当前 Demo 状态") or "（见材料）")
    return "\n".join(out)


def _first_after(lines: list[str], marker: str) -> str:
    for i, ln in enumerate(lines):
        if ln.strip() == marker:
            for nxt in lines[i + 1 : i + 8]:
                if nxt.strip() and not nxt.startswith("# "):
                    return nxt.strip()
    return ""
