"""DeziBee 需求存储：工作文件夹 + 需求清单 JSON。

数据规则（需求文档第十、十一节）：
 - 每个需求在工作文件夹下创建以「需求 ID」命名的子目录；该目录即需求全部数据所在。
 - 交互记录、摘要等结构化信息存于需求目录下的 dezibee.json（需求清单之外的冗余）。
   需求目录本身是唯一真源；清单 _index.json 仅用于快速列出需求。
 - 存储线程安全（全局锁），与 wokbee ProjectStore 同思路。
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

from dezibee.core.models import Requirement, _load_json, _save_json


_INDEX_NAME = "_index.json"  # 工作文件夹下需求清单
_REQ_META_NAME = "dezibee.json"  # 需求目录内元数据（需求 + 对话 + 摘要）

# 需求目录约定的子目录（DeziBee 核心是可部署的原型，不用 WokBee 运行管线目录）
REQ_SUBDIRS = ("demo", "prd", "uploads")

# 原型工作台模板源（src/dezibee/template/）：新需求预置三栏骨架进 demo/
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"
# 复制进 demo/ 的模板文件（相对模板根）
_TEMPLATE_FILES = ("index.html", "GUIDE.md")
_TEMPLATE_SUBDIRS = ("css", "js")
# 框架托管文件（版本随应用升级；不含用户数据，可安全覆盖）。
# index.html 含用户 WORKBENCH_DATA，绝不覆盖——升级只发生在用户首次创建时。
# 旧需求缺壳样式/缩放改造时，靠覆盖这三个文件补齐（框架 UI 由 JS 运行时注入，无需改 index.html）。
_TEMPLATE_FRAMEWORK_FILES = ("GUIDE.md", "css/workbench.css", "js/workbench.js")


def _seed_demo_from_template(demo_dir: Path) -> None:
    """把原型工作台模板复制进 demo/。

    - index.html 等用户数据文件：缺失才复制，绝不覆盖；
    - 框架托管文件（GUIDE.md / css / js）：随应用升级覆盖，保证存量需求
      也能拿到新版外壳/缩放框架（新壳 UI 由 JS 运行时注入，不依赖 index.html 变更）。
    """
    demo_dir.mkdir(parents=True, exist_ok=True)
    for rel in _TEMPLATE_FILES:
        src = _TEMPLATE_DIR / rel
        dst = demo_dir / rel
        if src.exists() and not dst.exists():
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    for sub in _TEMPLATE_SUBDIRS:
        sdir = _TEMPLATE_DIR / sub
        if not sdir.is_dir():
            continue
        for src in sdir.iterdir():
            if not src.is_file():
                continue
            dst_root = demo_dir / sub
            dst_root.mkdir(parents=True, exist_ok=True)
            dst = dst_root / src.name
            if not dst.exists():
                shutil.copy2(src, dst)
    # 框架文件升级：内容有变化才写，避免每次保存都动文件 mtime
    for rel in _TEMPLATE_FRAMEWORK_FILES:
        src = _TEMPLATE_DIR / rel
        dst = demo_dir / rel
        if not src.exists():
            continue
        new_text = src.read_text(encoding="utf-8")
        try:
            if not dst.exists() or dst.read_text(encoding="utf-8") != new_text:
                dst.write_text(new_text, encoding="utf-8")
        except OSError:
            pass

_lock = threading.RLock()


def _now() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class DeziBeeStore:
    """DeziBee 需求工作文件夹 + 需求清单。"""

    def __init__(self, work_root: str | Path | None = None):
        from wokbee.core.settings import WokBeeSettings

        if work_root is None:
            work_root = WokBeeSettings().dezibee_work_root
        self.work_root = Path(work_root or "")

    # ── 路径 ─────────────────────────────────────────────
    def req_dir(self, req_id: str) -> Path:
        from dezibee.core.models import _validate_req_id

        safe = _validate_req_id(req_id)
        return self.work_root / safe

    def req_meta_path(self, req_id: str) -> Path:
        return self.req_dir(req_id) / _REQ_META_NAME

    @property
    def index_path(self) -> Path:
        return self.work_root / _INDEX_NAME

    # ── 创建需求 ─────────────────────────────────────────
    def create(self, title: str = "", description: str = "") -> Requirement:
        """创建需求：生成 ID + 目录 + 元数据 + 清单条目。

        工作文件夹不存在时自动创建（需求文档「路径不存在时是否自动创建」→ 自动创建）。
        """
        with _lock:
            from dezibee.core.models import new_req_id

            root = self.work_root
            try:
                root.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise PermissionError(f"无法创建工作文件夹：{root}（{e}）") from e

            req = Requirement(
                id=new_req_id(store=self),
                title=(title or "未命名需求").strip()[:80] or "未命名需求",
                description=(description or "").strip(),
            )
            req_dir = self.req_dir(req.id)
            try:
                req_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise PermissionError(f"无法创建需求目录：{req_dir}（{e}）") from e
            for sub in REQ_SUBDIRS:
                (req_dir / sub).mkdir(parents=True, exist_ok=True)
            # 预置三栏原型工作台骨架（AI 在 WORKBENCH_DATA 上创作）
            _seed_demo_from_template(req_dir / "demo")

            self._write_req_meta(req)
            self._update_index(add=req)
            return req

    def _write_req_meta(self, req: Requirement) -> None:
        _save_json(self.req_meta_path(req.id), req.to_dict())

    def _read_req_meta(self, req_id: str) -> dict | None:
        return _load_json(self.req_meta_path(req_id))

    # ── 需求清单 ─────────────────────────────────────────
    def list_ids(self) -> list[str]:
        """从工作文件夹扫描需求 ID 目录（排除清单/非目录）。"""
        root = self.work_root
        if not root.exists():
            return []
        ids: list[str] = []
        for child in root.iterdir():
            if not child.is_dir() or child.name.startswith("_"):
                continue
            ids.append(child.name)
        # 稳定排序：REQ-… 按 ID 倒序（最新在前），其余按目录名
        def key(v: str) -> tuple[int, str]:
            return (0 if v.lower().startswith("req-") else 1, v)

        return sorted(ids, key=key, reverse=True)

    def list(self) -> list[Requirement]:
        """列出全部需求（按创建时间倒序；元数据缺失时以目录名/id 兜底）。"""
        with _lock:
            ids = self.list_ids()
            result: list[Requirement] = []
            for rid in ids:
                data = self._read_req_meta(rid)
                if data:
                    result.append(Requirement.from_dict(data))
                else:
                    # 目录存在但元数据缺失：以最小信息兜底
                    result.append(
                        Requirement(
                            id=rid,
                            title=rid,
                            description="（元数据缺失）",
                            created_at="",
                            updated_at="",
                        )
                    )
            # 置顶优先，其余按 created_at 倒序；无时间戳排最后
            result.sort(
                key=lambda r: (1 if r.pinned else 0, r.created_at or ""),
                reverse=True,
            )
            return result

    def get(self, req_id: str) -> Requirement | None:
        with _lock:
            data = self._read_req_meta(req_id)
            if not data:
                return None
            return Requirement.from_dict(data)

    # ── 保存 ─────────────────────────────────────────────
    def save(self, req: Requirement) -> None:
        """保存需求元数据（含对话记录与摘要）；目录不存在时补建（含工作台骨架）。"""
        with _lock:
            req.touch()
            req_dir = self.req_dir(req.id)
            req_dir.mkdir(parents=True, exist_ok=True)
            for sub in REQ_SUBDIRS:
                (req_dir / sub).mkdir(parents=True, exist_ok=True)
            _seed_demo_from_template(req_dir / "demo")
            self._write_req_meta(req)
            self._update_index(add=req)

    def _update_index(self, add: Requirement | None = None) -> None:
        """重建工作文件夹下 _index.json 需求清单。"""
        root = self.work_root
        if not root.exists():
            return
        entries: list[dict] = []
        for rid in self.list_ids():
            data = self._read_req_meta(rid)
            if data:
                entries.append(
                    {
                        "id": data.get("id") or rid,
                        "title": data.get("title") or rid,
                        "status": data.get("status") or "active",
                        "created_at": data.get("created_at") or "",
                        "updated_at": data.get("updated_at") or "",
                    }
                )
            else:
                entries.append(
                    {"id": rid, "title": rid, "status": "active", "created_at": "", "updated_at": ""}
                )
        entries.sort(key=lambda e: e.get("created_at") or "", reverse=True)
        _save_json(self.index_path, {"version": 1, "requirements": entries})

    def delete(self, req_id: str) -> bool:
        """删除需求目录与清单条目。"""
        with _lock:
            req_dir = self.req_dir(req_id)
            if not req_dir.exists():
                return False
            import shutil

            shutil.rmtree(req_dir, ignore_errors=True)
            self._update_index()
            return not req_dir.exists()

    # ── 校验 ─────────────────────────────────────────────
    @staticmethod
    def validate_work_root(path: str | Path) -> tuple[bool, str]:
        """校验工作文件夹是否可用：可写、可创建（需求文档「路径可写/Windows 路径」）。"""
        p = Path(path)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return False, f"无法创建目录：{e}"
        probe = p / ".dezibee_write_test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as e:
            return False, f"目录不可写：{e}"
        return True, ""
