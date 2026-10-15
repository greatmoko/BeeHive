"""从工具事件及成功轨迹提取脚本。"""
from __future__ import annotations
import ast
import json
import re
from typing import Any
from beehive.features.wokbee.pipeline.script_models import ScriptStep
from beehive.features.wokbee.pipeline.script_commands import _normalize_execute_command, _is_scriptable_execute
SCRIPTABLE_TOOLS = frozenset({"web_search", "http_get", "http_request", "execute"})

def _parse_tool_call_line(content: str) -> tuple[str, dict] | None:
    text = (content or "").strip()
    for prefix in (
        "call: ",
        "⟶ 调用工具：",
        "调用工具：",
        "1. 调用工具：",
        "1. call: ",
    ):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    text = re.sub(r"^\d+\.\s*", "", text)
    if text.lower().startswith("call:"):
        text = text.split(":", 1)[-1].strip()
    if "调用工具：" in text:
        text = text.split("调用工具：", 1)[-1].strip()
    m = re.match(r"^([A-Za-z_][\w]*)\((.*)\)\s*$", text, re.DOTALL)
    if not m:
        m2 = re.match(r"^([A-Za-z_][\w]*)\b", text)
        if m2 and m2.group(1) in SCRIPTABLE_TOOLS:
            return m2.group(1), {}
        return None
    name, args_s = m.group(1), m.group(2).strip()
    args: dict[str, Any] = {}
    if args_s and not args_s.endswith("…"):
        try:
            val = json.loads(args_s)
            if isinstance(val, dict):
                args = val
            else:
                args = {"raw": val}
        except json.JSONDecodeError:
            try:
                val = ast.literal_eval(args_s)
                if isinstance(val, dict):
                    args = val
                else:
                    args = {"raw": val}
            except (ValueError, SyntaxError):
                args = {"raw": args_s[:500]}
    elif args_s.endswith("…"):
        # 截断参数：尽量从 JSON 残片里抠 query/url
        m_q = re.search(r'"query"\s*:\s*"([^"]+)"', args_s) or re.search(
            r"'query'\s*:\s*'([^']+)'", args_s
        )
        m_u = re.search(r'"url"\s*:\s*"([^"]+)"', args_s) or re.search(
            r"'url'\s*:\s*'([^']+)'", args_s
        )
        if m_q:
            args = {"query": m_q.group(1)}
        elif m_u:
            args = {"url": m_u.group(1)}
        else:
            args = {"raw": args_s[:500]}
    return name, args

def extract_scriptable_from_events(events: list) -> list[ScriptStep]:
    steps: list[ScriptStep] = []
    seen: set[str] = set()

    def _try_add(name: str, args: dict[str, Any]) -> None:
        name = (name or "").strip()
        if name not in SCRIPTABLE_TOOLS:
            return
        args = dict(args or {})
        if name == "execute":
            raw = str(args.get("command") or args.get("raw") or "").strip()
            norm = _normalize_execute_command(raw)
            if not norm:
                return
            cmd, label = norm
            args = {"command": cmd, "label": label}
        key = f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
        if key in seen:
            return
        seen.add(key)
        if name == "execute":
            desc = f"execute({args.get('label')}: {args.get('command')})"
        else:
            desc = f"{name}({args})" if args else name
        steps.append(ScriptStep(tool=name, args=args, description=desc[:200]))

    for ev in events or []:
        kind = getattr(ev, "kind", "") or ""
        content = getattr(ev, "content", None) or ""
        meta = getattr(ev, "meta", None) or {}
        if not isinstance(meta, dict):
            meta = {}

        # 结构化 meta（推荐路径）：工具调用阶段直接带 tool/args
        if kind == "tool" and meta.get("phase") == "call":
            name = str(meta.get("tool") or "").strip()
            args = meta.get("args") if isinstance(meta.get("args"), dict) else {}
            _try_add(name, args)
            continue

        candidates = [content]
        if kind != "tool":
            candidates = content.splitlines()
        else:
            # 工具事件也按行扫，兼容「调用工具」写在首行
            candidates = [content] + content.splitlines()

        for line in candidates:
            parsed = _parse_tool_call_line(line)
            if not parsed:
                continue
            name, args = parsed
            _try_add(name, args)
    return steps

def extract_scriptable_from_path_text(success_path: str) -> list[ScriptStep]:
    class _E:
        def __init__(self, content: str, *, kind: str = "tool", meta: dict | None = None):
            self.kind = kind
            self.content = content
            self.meta = meta or {}

    events: list = []
    for line in (success_path or "").splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(_E(line))
        # 散文中夹带的脚本命令：python …/foo.py …
        if _is_scriptable_execute(line) or (
            ".py" in line.lower() and ("python" in line.lower() or "execute" in line.lower())
        ):
            # 尝试抠出可执行片段
            m = re.search(
                r"""(?ix)((?:python(?:3)?|py)\s+[\"']?[^\s\"']+\.(?:py|bat|cmd|ps1)[\"']?(?:\s+[^\n]*)?)""",
                line,
            )
            if m and _is_scriptable_execute(m.group(1)):
                # success_path 固定格式 `...:"{命令}";[说明]`：截掉 }" 之后被带进来的尾巴，
                # 否则与事件提取的同一命令 args 不一致，去重失败会固化出重复脚本。
                cmd = re.split(r'["}]\s*;?\s*\[', m.group(1))[0].rstrip('}"').strip()
                if cmd and _is_scriptable_execute(cmd):
                    events.append(
                        _E(
                            "",
                            kind="tool",
                            meta={
                                "phase": "call",
                                "tool": "execute",
                                "args": {"command": cmd},
                            },
                        )
                    )
    return extract_scriptable_from_events(events)
