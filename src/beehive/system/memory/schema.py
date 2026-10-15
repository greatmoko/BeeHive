"""既有记忆数据库的建表与兼容迁移。"""
import json
from beehive.system.memory.defaults import now, initial_global_memory


def initialize_memory(connect):
    with connect() as db:
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
        columns = {row["name"]: row["type"].upper() for row in db.execute("PRAGMA table_info(atomic_memory)")}
        proposal_columns = {row["name"] for row in db.execute("PRAGMA table_info(memory_proposals)")}
        if "operation" not in proposal_columns:
            db.execute("ALTER TABLE memory_proposals ADD COLUMN operation TEXT NOT NULL DEFAULT 'replace_module'")
        if columns.get("id") != "INTEGER":
            rows = db.execute("SELECT * FROM atomic_memory ORDER BY timestamp, id").fetchall()
            mapping = {row["id"]: index for index, row in enumerate(rows, 1)}
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("ALTER TABLE atomic_memory RENAME TO atomic_memory_legacy")
            db.execute("""CREATE TABLE atomic_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT, keywords TEXT NOT NULL, kind TEXT NOT NULL,
                body TEXT NOT NULL, file_url TEXT NOT NULL, timestamp TEXT NOT NULL,
                retrieval_count INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL,
                previous_id INTEGER REFERENCES atomic_memory(id))""")
            for row in rows:
                db.execute("INSERT INTO atomic_memory(id,keywords,kind,body,file_url,timestamp,retrieval_count,version,previous_id) VALUES (?,?,?,?,?,?,?,?,?)",
                           (mapping[row["id"]], row["keywords"], row["kind"], row["body"], row["file_url"], row["timestamp"], row["retrieval_count"], row["version"], mapping.get(row["previous_id"])))
            db.execute("DROP TABLE atomic_memory_legacy")
            db.execute("PRAGMA foreign_keys=ON")
        content = initial_global_memory()
        db.execute("INSERT OR IGNORE INTO global_memory(version,content,timestamp) VALUES (1,?,?)", (json.dumps(content, ensure_ascii=False), now()))
        current = db.execute("SELECT content FROM global_memory ORDER BY version DESC LIMIT 1").fetchone()
        if current:
            current_content = json.loads(current["content"])
            if "记忆使用规则" in current_content:
                legacy_rules = str(current_content.pop("记忆使用规则") or "").strip()
                rules = str(current_content.get("全局规则") or "").strip()
                if legacy_rules and legacy_rules not in rules:
                    rules = "\n".join(part for part in (rules, legacy_rules) if part)
                current_content["全局规则"] = rules
                db.execute("UPDATE global_memory SET content=?, timestamp=? WHERE version=(SELECT MAX(version) FROM global_memory)",
                           (json.dumps(current_content, ensure_ascii=False), now()))
                db.execute("UPDATE memory_proposals SET module='全局规则' WHERE module='记忆使用规则'")
        # Migrate older builds that stored one row per global-memory version.
        db.execute("DELETE FROM global_memory WHERE version < (SELECT MAX(version) FROM global_memory)")
