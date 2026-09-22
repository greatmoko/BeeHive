"""Three-layer memory storage. Session records are append-only Markdown, never SQLite."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

MODULES = ("用户画像", "环境信息", "全局规则", "记忆使用规则")
KINDS = ("事件", "事实", "规则", "偏好")
MEMORY_RULES = (
    "有关项目历史时按需读取会话记忆，不要把全文注入上下文。"
    "回答前分析意图并提取关键词，搜索原子记忆候选（默认50条），再按ID读取有用正文；"
    "结果不足时可用候选的新关键词二次搜索。用户明确要求记住时立即写入原子记忆。"
    "每轮结束检查新的长期事实、规则、偏好、事件以及本轮读取过的旧记忆是否需要修订。"
    "写入前先搜索去重；修订生成新ID并关联上一版本，保留适用条件不同的事实。"
    "全局记忆只能提出更新建议，用户明确点击是后才生效。"
)
_session_lock = threading.RLock()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(value):
    return " ".join(str(value or "").split())


class SessionMemory:
    def __init__(self, root: Path):
        self.path = Path(root) / ".wokbee" / "session_memory.md"

    def records(self):
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        records = []
        for index, line in enumerate(lines):
            match = re.fullmatch(r"<!-- turn:(\S+) -->", line)
            if match and index + 1 < len(lines):
                records.append({"turn_id": match[1], "round": len(records) + 1,
                                "line": index + 2, "text": lines[index + 1]})
        return records

    def append(self, turn_id: str, *, goal: str, result: str, unresolved: str = "无", keywords="", timestamp=None):
        if not re.fullmatch(r"[\w:.-]{1,160}", turn_id):
            raise ValueError("无效的轮次 ID")
        # Bound every field, including separators and labels, to < 1000 Unicode characters.
        text = (f"时间：{clean(timestamp or now())[:40]} → 用户需求：{clean(goal)[:220]}"
                f" → 处理结果：{clean(result)[:440]} → 未解决：{clean(unresolved)[:160]}"
                f" → 关键词：{clean(keywords)[:80]}")
        with _session_lock:
            existing = next((r for r in self.records() if r["turn_id"] == turn_id), None)
            if existing:
                return existing
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(f"\n<!-- turn:{turn_id} -->\n{text}\n")
                stream.flush()
            return self.records()[-1]

    def read(self, recent=5, rounds=None, keyword="", start_line=None, end_line=None):
        if start_line is not None or end_line is not None:
            start, end = int(start_line or 1), int(end_line or (int(start_line or 1) + 199))
            if start < 1 or end < start or end - start >= 500:
                raise ValueError("行范围须为正整数，单次最多500行")
            if not self.path.exists():
                return "暂无会话记忆"
            lines = self.path.read_text(encoding="utf-8").splitlines()
            return "\n".join(f"L{i + 1}: {lines[i]}" for i in range(start - 1, min(end, len(lines))))
        records = self.records()
        if rounds:
            wanted = {str(r) for r in rounds}
            records = [r for r in records if str(r["round"]) in wanted or r["turn_id"] in wanted]
        if keyword:
            records = [r for r in records if keyword.casefold() in r["text"].casefold()]
        records = records[-(50 if rounds else max(1, min(50, int(recent)))):]
        return "\n".join(f"第{r['round']}轮 [{r['turn_id']}] L{r['line']}: {r['text']}" for r in records) or "暂无匹配会话记忆"


class MemoryStore:
    def __init__(self, path: Path | None = None):
        if path is None:
            from tokbee.core.config import default_data_dir
            path = default_data_dir() / "memory.sqlite3"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS atomic_memory (
                    id TEXT PRIMARY KEY, keywords TEXT NOT NULL, kind TEXT NOT NULL,
                    body TEXT NOT NULL, file_url TEXT NOT NULL, timestamp TEXT NOT NULL,
                    retrieval_count INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL,
                    previous_id TEXT UNIQUE REFERENCES atomic_memory(id));
                CREATE TABLE IF NOT EXISTS global_memory (
                    version INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL, timestamp TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS memory_proposals (
                    id TEXT PRIMARY KEY, module TEXT NOT NULL, old TEXT NOT NULL,
                    new TEXT NOT NULL, reason TEXT NOT NULL, base_version INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', timestamp TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS memory_settings (key TEXT PRIMARY KEY, value REAL NOT NULL);
            """)
            content = {name: MEMORY_RULES if name == "记忆使用规则" else "" for name in MODULES}
            db.execute("INSERT OR IGNORE INTO global_memory(version,content,timestamp) VALUES (1,?,?)", (json.dumps(content, ensure_ascii=False), now()))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def search(self, keywords: list[str], limit=50):
        terms = list(dict.fromkeys(clean(k).casefold() for k in keywords if clean(k)))[:20]
        if not terms:
            return []
        # ponytail: literal keyword scan; introduce FTS when the memory corpus makes this slow.
        with self.connect() as db:
            rows = db.execute("SELECT id,keywords,kind,timestamp FROM atomic_memory a WHERE NOT EXISTS (SELECT 1 FROM atomic_memory b WHERE b.previous_id=a.id)").fetchall()
        scored = [(sum(term in row["keywords"].casefold() for term in terms), row) for row in rows]
        scored.sort(key=lambda item: (item[0], item[1]["timestamp"], item[1]["id"]), reverse=True)
        return [{"id": r["id"], "keywords": json.loads(r["keywords"]), "type": r["kind"]}
                for score, r in scored if score][:max(1, min(50, int(limit)))]

    def read(self, ids: list[str]):
        unique = list(dict.fromkeys(ids))
        if len(unique) > 50:
            raise ValueError("单次最多读取50条")
        rows = []
        with self.connect() as db:
            for ident in unique:
                row = db.execute("SELECT * FROM atomic_memory WHERE id=?", (ident,)).fetchone()
                if row:
                    db.execute("UPDATE atomic_memory SET retrieval_count=retrieval_count+1 WHERE id=?", (ident,))
                    value = dict(row)
                    value["keywords"] = json.loads(value["keywords"])
                    value["retrieval_count"] += 1
                    rows.append(value)
        return rows

    def write(self, keywords, kind, body, file_url="", previous_id=None):
        keywords = list(dict.fromkeys(clean(k) for k in keywords if clean(k)))[:20]
        body = str(body).strip()
        if kind not in KINDS or not body or len(body) > 10000 or not keywords:
            raise ValueError("需要关键词、有效类型及1~10000字的独立记忆正文")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            same = db.execute("SELECT id FROM atomic_memory a WHERE body=? AND kind=? AND file_url=? AND NOT EXISTS (SELECT 1 FROM atomic_memory b WHERE b.previous_id=a.id)", (body, kind, file_url)).fetchone()
            if same:
                return same["id"]
            version = 1
            if previous_id:
                old = db.execute("SELECT version FROM atomic_memory WHERE id=?", (previous_id,)).fetchone()
                if not old or db.execute("SELECT 1 FROM atomic_memory WHERE previous_id=?", (previous_id,)).fetchone():
                    raise ValueError("上一版本不存在或已被修订，请重新搜索最新版本")
                version = old["version"] + 1
            ident = uuid.uuid4().hex
            db.execute("INSERT INTO atomic_memory VALUES (?,?,?,?,?,?,0,?,?)",
                       (ident, json.dumps(keywords, ensure_ascii=False), kind, body, str(file_url), now(), version, previous_id or None))
            return ident

    def global_memory(self, version=None):
        with self.connect() as db:
            row = db.execute("SELECT * FROM global_memory WHERE version=?", (version,)).fetchone() if version else db.execute("SELECT * FROM global_memory ORDER BY version DESC LIMIT 1").fetchone()
        if row is None:
            raise ValueError("全局记忆版本不存在")
        return {"version": row["version"], "content": json.loads(row["content"])}

    @staticmethod
    def global_text(snapshot):
        return "\n\n".join(f"【{name}】\n{snapshot['content'].get(name, '')}" for name in MODULES)

    def propose(self, module, new, reason):
        if module not in MODULES or not isinstance(new, str) or not str(reason).strip():
            raise ValueError("无效的全局记忆更新建议")
        snapshot = self.global_memory()
        old = snapshot["content"][module]
        changed = {"content": {**snapshot["content"], module: new}}
        if len(self.global_text(changed)) > 5000:
            raise ValueError("全局记忆总长度不可超过5000字")
        if old == new:
            return None
        with self.connect() as db:
            row = db.execute("SELECT id FROM memory_proposals WHERE module=? AND old=? AND new=? AND status='pending'", (module, old, new)).fetchone()
            if row:
                return row["id"]
            ident = uuid.uuid4().hex
            db.execute("INSERT INTO memory_proposals VALUES (?,?,?,?,?,?,'pending',?)",
                       (ident, module, old, new, str(reason), snapshot["version"], now()))
        return ident

    def proposals(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM memory_proposals WHERE status='pending' ORDER BY timestamp")]

    def decide(self, ident, *, accept=False):
        """UI-only entry point. Never expose this as an AI tool."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            proposal = db.execute("SELECT * FROM memory_proposals WHERE id=? AND status='pending'", (ident,)).fetchone()
            if not proposal:
                raise ValueError("建议已处理或不存在")
            if accept:
                current = db.execute("SELECT * FROM global_memory ORDER BY version DESC LIMIT 1").fetchone()
                content = json.loads(current["content"])
                if content[proposal["module"]] != proposal["old"]:
                    raise ValueError("该模块已更新，旧建议不能覆盖当前内容")
                content[proposal["module"]] = proposal["new"]
                if len(self.global_text({"content": content})) > 5000:
                    raise ValueError("全局记忆总长度不可超过5000字")
                db.execute("INSERT INTO global_memory(content,timestamp) VALUES (?,?)", (json.dumps(content, ensure_ascii=False), now()))
            db.execute("UPDATE memory_proposals SET status=? WHERE id=?", ("accepted" if accept else "rejected", ident))

    def restore(self, version):
        snapshot = self.global_memory(version)
        with self.connect() as db:
            db.execute("INSERT INTO global_memory(content,timestamp) VALUES (?,?)", (json.dumps(snapshot["content"], ensure_ascii=False), now()))

    def thresholds(self):
        with self.connect() as db:
            values = dict(db.execute("SELECT key,value FROM memory_settings"))
        return values.get("trigger", .8), values.get("target", .3)

    def set_thresholds(self, trigger, target):
        if not 0 < target < trigger <= 1:
            raise ValueError("必须满足 0 < 压缩目标 < 触发比例 ≤ 100%")
        with self.connect() as db:
            db.executemany("INSERT OR REPLACE INTO memory_settings VALUES (?,?)", [("trigger", trigger), ("target", target)])
