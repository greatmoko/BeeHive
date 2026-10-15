"""全局与原子记忆持久化。"""
from __future__ import annotations
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from beehive.system.memory.defaults import MODULES, KINDS, now, clean, initial_global_memory
from beehive.system.memory.schema import initialize_memory


class MemoryStore:
    def __init__(self, path: Path | None = None):
        if path is None:
            from beehive.system.settings.config import default_data_dir
            path = default_data_dir() / "memory.sqlite3"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        initialize_memory(self.connect)

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
        def matches(term, stored):
            if term in stored or stored in term:
                return True
            parts = re.findall(r"[\u4e00-\u9fff]{2}|[a-z0-9]{3,}", term)
            return any(part in stored for part in parts)
        # ponytail: literal keyword scan; introduce FTS when the memory corpus makes this slow.
        with self.connect() as db:
            rows = db.execute("SELECT id,keywords,kind,timestamp FROM atomic_memory a WHERE NOT EXISTS (SELECT 1 FROM atomic_memory b WHERE b.previous_id=a.id)").fetchall()
        scored = [(sum(matches(term, row["keywords"].casefold()) for term in terms), row) for row in rows]
        scored.sort(key=lambda item: (item[0], item[1]["timestamp"], item[1]["id"]), reverse=True)
        return [{"id": r["id"], "keywords": json.loads(r["keywords"]), "type": r["kind"]}
                for score, r in scored if score][:max(1, min(50, int(limit)))]

    def recent(self, limit=10):
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,keywords,kind,body,file_url,timestamp,retrieval_count,version,previous_id "
                "FROM atomic_memory a WHERE NOT EXISTS (SELECT 1 FROM atomic_memory b WHERE b.previous_id=a.id) "
                "ORDER BY timestamp DESC LIMIT ?", (max(1, min(50, int(limit))),)
            ).fetchall()
        return [{**dict(row), "keywords": json.loads(row["keywords"]), "type": row["kind"]} for row in rows]

    def search_full(self, keywords, limit=10):
        ids = [row["id"] for row in self.search(keywords, limit)]
        if not ids:
            return []
        with self.connect() as db:
            rows = [db.execute("SELECT * FROM atomic_memory WHERE id=?", (ident,)).fetchone() for ident in ids]
        return [{**dict(row), "keywords": json.loads(row["keywords"]), "type": row["kind"]} for row in rows if row]

    def clear_atomic(self):
        with self.connect() as db:
            db.execute("DELETE FROM atomic_memory")

    def delete(self, ident):
        with self.connect() as db:
            row = db.execute("SELECT id FROM atomic_memory WHERE id=?", (str(ident),)).fetchone()
            if not row:
                raise ValueError("原子记忆不存在")
            child = db.execute("SELECT id FROM atomic_memory WHERE previous_id=?", (str(ident),)).fetchone()
            if child:
                raise ValueError("该记忆已有新版本，不能删除历史版本")
            db.execute("DELETE FROM atomic_memory WHERE id=?", (str(ident),))

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
            cursor = db.execute("INSERT INTO atomic_memory(keywords,kind,body,file_url,timestamp,retrieval_count,version,previous_id) VALUES (?,?,?,?,?,0,?,?)",
                                (json.dumps(keywords, ensure_ascii=False), kind, body, str(file_url), now(), version, int(previous_id) if previous_id else None))
            return int(cursor.lastrowid)

    def global_memory(self, version=None):
        """Return the single current global memory document.

        ``version`` is accepted for compatibility with older callers but is
        intentionally ignored; global memory no longer has user-visible
        versions or history.
        """
        with self.connect() as db:
            row = db.execute("SELECT * FROM global_memory ORDER BY version DESC LIMIT 1").fetchone()
        if row is None:
            raise ValueError("全局记忆不存在")
        return {"content": json.loads(row["content"])}

    @staticmethod
    def global_text(snapshot):
        return "\n\n".join(f"【{name}】\n{snapshot['content'].get(name, '')}" for name in MODULES)

    def ensure_environment(self, environment):
        """Fill the environment module once, without overwriting user edits."""
        environment = str(environment or "").strip()
        if not environment:
            return False
        current = self.global_memory()
        if current["content"].get("环境信息", "").strip():
            return False
        content = {**current["content"], "环境信息": environment}
        if len(self.global_text({"content": content})) > 50000:
            raise ValueError("全局记忆总长度不可超过50000字符")
        with self.connect() as db:
            db.execute(
                "UPDATE global_memory SET content=?, timestamp=?",
                (json.dumps(content, ensure_ascii=False), now()),
            )
        return True

    def reset_global_memory(self, environment=""):
        """Restore the initial template and discard pending update proposals."""
        content = initial_global_memory(environment)
        if len(self.global_text({"content": content})) > 50000:
            raise ValueError("全局记忆总长度不可超过50000字符")
        current = self.global_memory()
        changed = content != current["content"]
        with self.connect() as db:
            db.execute(
                "UPDATE global_memory SET content=?, timestamp=?",
                (json.dumps(content, ensure_ascii=False), now()),
            )
            db.execute("UPDATE memory_proposals SET status='rejected' WHERE status='pending'")
        return changed

    @staticmethod
    def _rules(text):
        return [line.strip() for line in str(text or "").splitlines() if line.strip()]

    def propose_rule(self, module, operation, new, reason, target=""):
        if module not in MODULES:
            raise ValueError(f"module 必须是以下之一：{'、'.join(MODULES)}")
        if operation not in ("add", "replace"):
            raise ValueError("operation 必须是 add 或 replace")
        if not str(new).strip():
            raise ValueError("new 不能为空")
        if not str(reason).strip():
            raise ValueError("reason 不能为空")
        snapshot = self.global_memory()
        old, new = str(target or "").strip(), str(new).strip()
        new = re.sub(rf"^(?:【{re.escape(module)}】|{re.escape(module)})\s*[:：]?\s*", "", new, count=1)
        if not new:
            raise ValueError("new 不能为空")
        rules = self._rules(snapshot["content"][module])
        if "\n" in new or len(new) > 500:
            raise ValueError("全局记忆建议一次只能新增或替换一条不超过500字的规则")
        if operation == "add":
            if old or new in rules:
                raise ValueError("新增规则不得指定旧规则，且不能与现有规则重复")
            updated = [*rules, new]
        else:
            if not old or old not in rules or old == new:
                raise ValueError("替换必须提供当前存在的完整旧规则，且新旧内容不同")
            updated = [new if item == old else item for item in rules]
        changed = {"content": {**snapshot["content"], module: "\n".join(updated)}}
        if len(self.global_text(changed)) > 50000:
            raise ValueError("全局记忆总长度不可超过50000字符")
        with self.connect() as db:
            row = db.execute("SELECT id FROM memory_proposals WHERE module=? AND old=? AND new=? AND operation=? AND status='pending'", (module, old, new, operation)).fetchone()
            if row:
                return row["id"]
            ident = uuid.uuid4().hex
            db.execute("INSERT INTO memory_proposals(id,module,old,new,reason,base_version,status,timestamp,operation) VALUES (?,?,?,?,?,?,?,?,?)",
                       (ident, module, old, new, str(reason), 1, "pending", now(), operation))
        return ident

    def propose(self, module, new, reason):
        """Compatibility helper: append one rule instead of replacing a module."""
        return self.propose_rule(module, "add", new, reason)

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
                rules = self._rules(content[proposal["module"]])
                if proposal["operation"] == "add":
                    if proposal["new"] in rules:
                        raise ValueError("该规则已存在，无需重复新增")
                    rules.append(proposal["new"])
                elif proposal["operation"] == "replace":
                    if proposal["old"] not in rules:
                        raise ValueError("目标规则已变化，不能按错误位置替换")
                    rules = [proposal["new"] if item == proposal["old"] else item for item in rules]
                else:
                    raise ValueError("旧版整模块更新建议已失效，请重新按条提交")
                content[proposal["module"]] = "\n".join(rules)
                if len(self.global_text({"content": content})) > 50000:
                    raise ValueError("全局记忆总长度不可超过50000字符")
                db.execute("UPDATE global_memory SET content=?, timestamp=?", (json.dumps(content, ensure_ascii=False), now()))
            db.execute("UPDATE memory_proposals SET status=? WHERE id=?", ("accepted" if accept else "rejected", ident))

    def save_global(self, content):
        """Replace the single user-editable global memory document."""
        if not isinstance(content, dict) or any(name not in content for name in MODULES):
            raise ValueError("全局记忆必须包含用户画像、环境信息、全局规则三个模块")
        normalized = {name: str(content.get(name) or "").strip() for name in MODULES}
        if len(self.global_text({"content": normalized})) > 50000:
            raise ValueError("全局记忆总长度不可超过50000字符")
        current = self.global_memory()
        if normalized == current["content"]:
            return False
        with self.connect() as db:
            db.execute("UPDATE global_memory SET content=?, timestamp=?", (json.dumps(normalized, ensure_ascii=False), now()))
        return True

    def thresholds(self):
        with self.connect() as db:
            values = dict(db.execute("SELECT key,value FROM memory_settings"))
        return values.get("trigger", .8), values.get("target", .3)

    def set_thresholds(self, trigger, target):
        if not 0 < target < trigger <= 1:
            raise ValueError("必须满足 0 < 压缩目标 < 触发比例 ≤ 100%")
        with self.connect() as db:
            db.executemany("INSERT OR REPLACE INTO memory_settings VALUES (?,?)", [("trigger", trigger), ("target", target)])
