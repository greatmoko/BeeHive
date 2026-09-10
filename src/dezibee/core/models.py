"""DeziBee 需求数据模型。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today_compact() -> str:
    return datetime.now().strftime("%Y%m%d")


def _load_json(path: Path) -> Any:
    """读取 JSON；文件缺失/损坏返回 None（不抛错）。"""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, TypeError):
        return None


def _save_json(path: Path, data: Any) -> None:
    """原子写 JSON（先写临时文件再替换），与 wokbee safe_write_json 同思路。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def _validate_req_id(req_id: str) -> str:
    """需求 ID 规范化：非空、文件名安全、限长。"""
    raw = str(req_id or "").strip()
    if not raw:
        raise ValueError("需求 ID 不能为空")
    safe = "".join(
        ch if (ch.isalnum() or ch in "-_.") else "-" for ch in raw
    )
    if not safe:
        raise ValueError("需求 ID 不合法")
    return safe[:64]


def new_req_id(preferred: str = "", *, store=None) -> str:
    """生成需求 ID。

    优先采用用户偏好（文件系统安全的改名版本）；否则自动生成
    REQ-<YYYYMMDD>-<3 位序号>；当日序号冲突时自动递增（并发安全：带锁重扫）。
    store：DeziBeeStore 实例（用于占用检查）；缺省用默认工作文件夹的单例。
    """
    candidate = _validate_req_id(preferred) if preferred else ""
    if candidate:
        return candidate
    seq = 1
    while seq < 1000:
        rid = f"REQ-{_today_compact()}-{seq:03d}"
        if not _req_id_taken(rid, store=store):
            return rid
        seq += 1
    return f"REQ-{_today_compact()}-{uuid.uuid4().hex[:4].upper()}"


def _req_id_taken(req_id: str, *, store=None) -> bool:
    """判断该需求 ID 是否已被使用（扫描工作文件夹下目录 + 需求清单）。"""
    from dezibee.core.store import DeziBeeStore  # 延迟导入避免循环

    store = store or DeziBeeStore()
    exists = store.req_dir(req_id).exists() or req_id in store.list_ids()
    if not exists:
        # 并发兜底：再往工作文件夹下扫一次真实目录（含未入库）
        root = store.work_root
        if root.exists():
            for child in root.iterdir():
                if child.is_dir() and child.name.lower() == req_id.lower():
                    return True
    return exists


@dataclass
class Conversation:
    """需求下的一个对话（同属一个需求，可另起多个）。"""

    conv_id: str = field(default_factory=lambda: f"conv_{uuid.uuid4().hex[:10]}")
    title: str = "主对话"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    summary: str = ""  # 最新 Context Summary（markdown），另起对话时注入
    events: list[dict] = field(default_factory=list)  # ProjectEvent.to_dict() 列表

    def touch(self) -> None:
        self.updated_at = _now()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Conversation:
        data = data or {}
        return cls(
            conv_id=str(data.get("conv_id") or f"conv_{uuid.uuid4().hex[:10]}"),
            title=str(data.get("title") or "对话"),
            created_at=str(data.get("created_at") or _now()),
            updated_at=str(data.get("updated_at") or _now()),
            summary=str(data.get("summary") or ""),
            events=list(data.get("events") or []),
        )


@dataclass
class Requirement:
    """DeziBee 需求。目录 id = 工作文件夹下的需求 ID。"""

    id: str = field(default_factory=new_req_id)
    title: str = "未命名需求"
    description: str = ""
    status: str = "active"  # active | archived
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    # 绑定模型（复用 ProviderStore）；空 = 使用全局默认
    provider: str = ""
    model_id: str = ""
    provider_name: str = ""  # 展示用缓存
    model_label: str = ""  # 展示用缓存
    # 交互记录
    conversations: list[Conversation] = field(default_factory=list)
    # 设计上下文摘要（会话间继承）
    context_summary: str = ""
    # 当前选中的对话 id（UI 状态，不参与来源）
    active_conv_id: str = ""

    def touch(self) -> None:
        self.updated_at = _now()

    def active_conversation(self) -> Conversation:
        if not self.conversations:
            self.conversations.append(Conversation())
        for c in self.conversations:
            if c.conv_id == self.active_conv_id:
                return c
        self.active_conv_id = self.conversations[-1].conv_id
        return self.conversations[-1]

    def to_dict(self) -> dict:
        d = asdict(self)
        # 序列化 conversations
        d["conversations"] = [c.to_dict() for c in self.conversations]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> Requirement:
        data = data or {}
        req = cls(
            id=str(data.get("id") or new_req_id()),
            title=str(data.get("title") or "未命名需求"),
            description=str(data.get("description") or ""),
            status=str(data.get("status") or "active"),
            created_at=str(data.get("created_at") or _now()),
            updated_at=str(data.get("updated_at") or _now()),
            provider=str(data.get("provider") or ""),
            model_id=str(data.get("model_id") or ""),
            provider_name=str(data.get("provider_name") or ""),
            model_label=str(data.get("model_label") or ""),
            context_summary=str(data.get("context_summary") or ""),
            active_conv_id=str(data.get("active_conv_id") or ""),
        )
        req.conversations = [
            Conversation.from_dict(c)
            for c in (data.get("conversations") or [])
            if isinstance(c, dict)
        ]
        return req

    # ── 目录约定 ─────────────────────────────────────────
    @property
    def root(self) -> Path:
        """需求 ID 工作目录。"""
        from dezibee.core.store import DeziBeeStore

        return DeziBeeStore().req_dir(self.id)

    @property
    def demo_dir(self) -> Path:
        return self.root / "demo"

    @property
    def prd_dir(self) -> Path:
        return self.root / "prd"

    @property
    def index_file(self) -> Path:
        return self.demo_dir / "index.html"
