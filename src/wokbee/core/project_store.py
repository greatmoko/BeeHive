"""项目存储：工作区根目录下按 project_id 建专属文件夹。"""

from __future__ import annotations

import json
import logging
import shutil
import threading
from datetime import datetime
from pathlib import Path

from tokbee.core.safe_io import safe_write_json, safe_write_text

from wokbee.core.models import (
    ApprovalFlags,
    Project,
    ProjectEvent,
    ProjectStatus,
    new_project_id,
    MAX_PROJECT_TITLE_LEN,
    _now,
)
from wokbee.core.paths import (
    ARCHIVABLE_DIRS,
    archives_dir,
    ensure_project_layout,
    events_path,
    meta_path,
    project_dir,
)
from wokbee.core.settings import WokBeeSettings
from wokbee.core.project_store_events import ProjectStoreEventsMixin, MAX_ARCHIVES
from wokbee.core.project_store_filesystem import (
    ProjectStoreFilesystemMixin,
    TRASH_RETENTION_DAYS,
)
from wokbee.core.project_store_support import (
    _META_LOCK,
    _EVENTS_LOCK,
    _event_lines_cache,
    _event_for_log,
    EVENT_LOG_MAX_CHARS,
)

logger = logging.getLogger("wokbee")

_TRASHED_AT_NAME = "_trashed_at"


def _clean_text(value: str | None) -> str:
    """去掉 NUL 等脏字符，避免标题/目标异常。"""
    return (value or "").replace("\x00", "").strip()


def _normalize_title(value: str | None) -> str:
    """项目名称：去脏字符并截断到上限。"""
    title = _clean_text(value)
    if len(title) > MAX_PROJECT_TITLE_LEN:
        title = title[:MAX_PROJECT_TITLE_LEN]
    return title


class ProjectStore(ProjectStoreFilesystemMixin, ProjectStoreEventsMixin):
    """管理 WokBee 项目生命周期与磁盘布局。"""

    def __init__(self, settings: WokBeeSettings | None = None):
        self.settings = settings or WokBeeSettings()
        self._ensure_workspace()

    def list_projects(self) -> list[Project]:
        root = self.workspace_root
        if not root.exists():
            return []
        projects: list[Project] = []
        for child in root.iterdir():
            if not child.is_dir() or child.name.startswith("_"):
                continue
            meta = meta_path(child)
            if not meta.exists():
                continue
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                projects.append(Project.from_dict(data))
            except (json.JSONDecodeError, OSError, TypeError) as e:
                logger.warning("跳过损坏的项目元数据 %s: %s", meta, e)
        pinned = sorted(
            [p for p in projects if p.pinned],
            key=lambda p: p.pinned_at or p.created_at,
            reverse=True,
        )
        normal = sorted(
            [p for p in projects if not p.pinned],
            key=lambda p: p.created_at,
            reverse=True,
        )
        return pinned + normal

    def search(self, keyword: str) -> list[Project]:
        kw = (keyword or "").strip().lower()
        items = self.list_projects()
        if not kw:
            return items
        return [
            p for p in items
            if kw in p.title.lower() or kw in p.id.lower() or kw in (p.goal or "").lower()
        ]

    def get(self, project_id: str) -> Project | None:
        meta = meta_path(self.path_for(project_id))
        if not meta.exists():
            return None
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            return Project.from_dict(data)
        except (json.JSONDecodeError, OSError):
            return None

    def save(self, project: Project) -> None:
        with _META_LOCK:
            project.title = _normalize_title(project.title) or "未命名项目"
            project.goal = _clean_text(project.goal)
            project.touch()
            root = self.path_for(project.id)
            ensure_project_layout(root)
            safe_write_json(meta_path(root), project.to_dict())
            # 只更新本项目索引条目，避免每次保存/事件都全量扫描全部项目（O(n²) 卡顿）
            self._touch_index_entry(project)

    def patch(self, project_id: str, **fields) -> Project | None:
        """原子更新若干字段：持锁下重新读盘再写，避免并行工具互相覆盖。"""
        with _META_LOCK:
            project = self.get(project_id)
            if not project:
                return None
            if "title" in fields:
                title = _normalize_title(fields.get("title"))
                if title:
                    project.title = title
            if "goal" in fields:
                project.goal = _clean_text(fields.get("goal"))
            if "approval" in fields and fields["approval"] is not None:
                project.approval = fields["approval"].copy()
            if "status" in fields and fields["status"] is not None:
                project.status = fields["status"]
            if "current_step" in fields and fields["current_step"] is not None:
                project.current_step = str(fields["current_step"])
            if "progress_done" in fields and fields["progress_done"] is not None:
                project.progress_done = int(fields["progress_done"])
            if "progress_total" in fields and fields["progress_total"] is not None:
                project.progress_total = int(fields["progress_total"])
            if "artifacts_summary" in fields and fields["artifacts_summary"] is not None:
                project.artifacts_summary = str(fields["artifacts_summary"])
            if "provider" in fields and fields["provider"] is not None:
                project.provider = str(fields["provider"])
            if "model_id" in fields and fields["model_id"] is not None:
                project.model_id = str(fields["model_id"])
            if "pinned" in fields and fields["pinned"] is not None:
                new_pinned = bool(fields["pinned"])
                if new_pinned and not project.pinned:
                    project.pinned_at = _now()
                elif not new_pinned:
                    project.pinned_at = ""
                project.pinned = new_pinned
            if "pinned_at" in fields and fields["pinned_at"] is not None:
                project.pinned_at = str(fields["pinned_at"])
            # 直接写盘（已在锁内，勿再进 save 的同锁递归也可，RLock 安全）
            self.save(project)
            return project

    def create(
        self,
        title: str = "",
        goal: str = "",
        *,
        approval: ApprovalFlags | None = None,
    ) -> Project:
        pid = new_project_id()
        # 新建时拷贝全局审核勾选，之后项目可独立修改
        flags = (approval or self.settings.approval).copy()
        # 新建项目：优先厂商设置「默认模型」（用户在模型旁点的「默认」），再回退 WokBee 设置
        provider = ""
        model_id = ""
        display = ""
        try:
            from tokbee.core.provider_store import ProviderStore

            default = ProviderStore().resolve_default()
            if default:
                provider = default.provider_id
                model_id = default.model_id
                display = f"{default.provider_name} / {default.model_id}"
        except Exception:
            logger.exception("读取厂商默认模型失败")
        if not (provider and model_id):
            provider = (self.settings.default_provider or "").strip()
            model_id = (self.settings.default_model_id or "").strip()
            if provider and model_id:
                display = f"{provider}/{model_id}"
        project = Project(
            id=pid,
            title=_normalize_title(title) or "未命名项目",
            goal=_clean_text(goal),
            approval=flags,
            provider=provider,
            model_id=model_id,
            status=ProjectStatus.IDLE,
        )
        self.save(project)
        self.append_event(
            pid,
            ProjectEvent(
                kind="info",
                content=(
                    f"项目已创建。工作目录：{self.path_for(pid)}\n"
                    f"审核策略（继承自全局，可单独修改）：{flags.summary()}"
                    + (
                        f"\n默认模型：{display or f'{provider}/{model_id}'}"
                        if provider and model_id
                        else "\n未绑定模型：将使用厂商默认或列表第一个可用模型。"
                    )
                ),
            ),
        )
        return project

    def rename(self, project_id: str, new_title: str) -> Project | None:
        return self.patch(project_id, title=new_title)

    def update_goal(self, project_id: str, goal: str) -> Project | None:
        return self.patch(project_id, goal=goal)

    def set_approval(self, project_id: str, approval: ApprovalFlags) -> Project | None:
        return self.patch(project_id, approval=approval)

    def set_status(
        self,
        project_id: str,
        status: ProjectStatus,
        *,
        current_step: str | None = None,
        progress_done: int | None = None,
        progress_total: int | None = None,
    ) -> Project | None:
        fields: dict = {"status": status}
        if current_step is not None:
            fields["current_step"] = current_step
        if progress_done is not None:
            fields["progress_done"] = progress_done
        if progress_total is not None:
            fields["progress_total"] = progress_total
        return self.patch(project_id, **fields)

    def toggle_pin(self, project_id: str) -> Project | None:
        """切换置顶；置顶项目不可删除，防止误操作。"""
        project = self.get(project_id)
        if not project:
            return None
        return self.patch(project_id, pinned=not project.pinned)

    def delete(self, project_id: str, *, trash: bool = True) -> bool:
        project = self.get(project_id)
        if project and project.pinned:
            logger.info("拒绝删除置顶项目：%s", project_id)
            return False
        if project and project.status in (
            ProjectStatus.RUNNING, ProjectStatus.AWAITING_APPROVAL,
        ):
            logger.info("拒绝删除运行中项目：%s", project_id)
            return False
        root = self.path_for(project_id)
        if not root.exists():
            return False
        if trash:
            trash_root = self.trash_root()
            dest = trash_root / project_id
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            shutil.move(str(root), str(dest))
            self._mark_trashed(dest)
        else:
            shutil.rmtree(root, ignore_errors=True)
        self._rebuild_index()
        # 删除后顺带清一次过期项
        self.purge_expired_trash()
        return True

    def delete_unpinned(self, *, trash: bool = True) -> int:
        """删除全部未置顶项目；置顶与运行中项目跳过。单条逻辑与 ``delete`` / 侧栏删除一致。"""
        ids = [p.id for p in self.list_projects() if not p.pinned]
        deleted = 0
        for pid in ids:
            if self.delete(pid, trash=trash):
                deleted += 1
        return deleted

    def effective_approval(self, project: Project) -> ApprovalFlags:
        """项目自带审核策略（创建时已从全局拷贝，之后独立）。"""
        return project.approval.copy()
