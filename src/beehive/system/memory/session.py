"""会话记忆：从 memory/events.jsonl 的轮次事件推导，不再另存 session_memory.md。

- 每轮的摘要（用户需求 / 处理结果 / 未解决）由该轮事件机械推导：排序与截断
  全部在本地代码完成，不额外消耗 AI Token，也不重复保存另一份事件日志。
- 旧版项目的 memory/session_memory.md（及更早的 .wokbee/session_memory.md）
  只读兼容：文件存在且无新事件时按旧标记解析。
"""
from __future__ import annotations
import json
import re
import threading
from pathlib import Path
from beehive.system.memory.defaults import clean, now

_session_lock = threading.RLock()

_TURN_MARK_RE = re.compile(r"<!-- turn:(\S+) -->")


class SessionMemory:
    def __init__(self, root: Path):
        self.root = Path(root)
        # WokBee 项目和 DeziBee 需求都把工作根目录传进来。
        self.path = self.root / "memory" / "events.jsonl"
        # 旧版会话记忆位置（只读兼容）。
        self.legacy_path = self.root / "memory" / "session_memory.md"
        if not self.legacy_path.exists():
            older = self.root / ".wokbee" / "session_memory.md"
            if older.exists():
                self.legacy_path = older

    # -------------------------------------------------- 事件文件读取

    def _round_rows(self) -> list[dict]:
        """解析 events.jsonl，按轮次号分组返回轮次行列表。

        轮次头字段（round_id/round_started_at/user_input/goal）只在该轮首
        事件行上；每行都有 round，直接按 round 分组即可。
        """
        rows: list[dict] = []
        try:
            if not self.path.exists():
                return rows
            with self.path.open("r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(data, dict):
                        continue
                    rno = data.get("round")
                    if not isinstance(rno, int) or rno < 1:
                        continue
                    rows.append({**data, "_line": line_no})
        except OSError:
            return []
        return rows

    @staticmethod
    def _summarize_round(rows: list[dict]) -> str:
        """把一轮的事件压成旧版五段式单行摘要（字段级截断，整体 <1000 字符）。"""
        first = rows[0]
        goal = clean(str(first.get("user_input") or first.get("goal") or ""))
        result = ""
        unresolved = "无"
        for row in rows:
            etype = str(row.get("type") or "")
            content = (str(row.get("content") or "") or "").strip()
            if etype == "ai_reply" and content:
                result = content
            elif etype == "error" and content and unresolved == "无":
                unresolved = content
        return (
            f"时间：{clean(str(first.get('round_started_at') or first.get('time') or now()))[:40]}"
            f" → 用户需求：{goal[:220]}"
            f" → 处理结果：{clean(result)[:440]}"
            f" → 未解决：{clean(unresolved)[:160]}"
            f" → 关键词："
        )

    # -------------------------------------------------- 公开接口

    def records(self):
        """轮次列表：{turn_id, round, line, text}（line 为该轮首事件所在文件行）。"""
        rows = self._round_rows()
        if rows:
            records = []
            by_round: dict[int, list[dict]] = {}
            for row in rows:
                by_round.setdefault(int(row["round"]), []).append(row)
            for order, (rno, group) in enumerate(sorted(by_round.items()), start=1):
                head = group[0]
                records.append({
                    "turn_id": str(head.get("round_id") or f"r{rno}"),
                    "round": rno,
                    "line": head["_line"],
                    "text": self._summarize_round(group),
                })
            return records
        return self._legacy_records()

    def _legacy_records(self):
        if not self.legacy_path.exists():
            return []
        try:
            lines = self.legacy_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        records = []
        for index, line in enumerate(lines):
            match = _TURN_MARK_RE.fullmatch(line)
            if match and index + 1 < len(lines):
                records.append({"turn_id": match[1], "round": len(records) + 1,
                                "line": index + 2, "text": lines[index + 1]})
        return records

    def append(self, turn_id: str, *, goal: str, result: str, unresolved: str = "无", keywords="", timestamp=None):
        """兼容旧调用：轮次记录已随事件流写入 events.jsonl，这里只做幂等返回。

        新轮次尚未落盘时（如压缩路径补写），用传入字段拼一行摘要，不再写文件。
        """
        if not re.fullmatch(r"[\w:.-]{1,160}", turn_id):
            raise ValueError("无效的轮次 ID")
        with _session_lock:
            existing = next((r for r in self.records() if r["turn_id"] == turn_id), None)
            if existing:
                return existing
        text = (f"时间：{clean(timestamp or now())[:40]} → 用户需求：{clean(goal)[:220]}"
                f" → 处理结果：{clean(result)[:440]} → 未解决：{clean(unresolved)[:160]}"
                f" → 关键词：{clean(keywords)[:80]}")
        return {"turn_id": turn_id, "round": len(self.records()) + 1, "line": 0, "text": text}

    def read(self, recent=5, rounds=None, keyword="", start_line=None, end_line=None):
        if start_line is not None or end_line is not None:
            # 行范围语义改为 events.jsonl 原始行（旧版是 session_memory.md 行）。
            start, end = int(start_line or 1), int(end_line or (int(start_line or 1) + 199))
            if start < 1 or end < start or end - start >= 500:
                raise ValueError("行范围须为正整数，单次最多500行")
            source = self.path if self.path.exists() else self.legacy_path
            if not source.exists():
                return "暂无会话记忆"
            try:
                lines = source.read_text(encoding="utf-8").splitlines()
            except OSError:
                return "暂无会话记忆"
            return "\n".join(f"L{i + 1}: {lines[i]}" for i in range(start - 1, min(end, len(lines))))
        records = self.records()
        if rounds:
            wanted = {str(r) for r in rounds}
            records = [r for r in records if str(r["round"]) in wanted or r["turn_id"] in wanted]
        if keyword:
            records = [r for r in records if keyword.casefold() in r["text"].casefold()]
        records = records[-(50 if rounds else max(1, min(50, int(recent)))):]
        return "\n".join(f"第{r['round']}轮 [{r['turn_id']}] L{r['line']}: {r['text']}" for r in records) or "暂无匹配会话记忆"
