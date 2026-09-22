"""ProjectStore filesystem and workspace layout responsibilities."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from tokbee.core.safe_io import safe_write_json

from wokbee.core.models import Project
from wokbee.core.paths import project_dir
from wokbee.core.settings import WokBeeSettings
from wokbee.core.project_store_support import _META_LOCK

logger = logging.getLogger("wokbee")
TRASH_RETENTION_DAYS = 7
_TRASHED_AT_NAME = "_trashed_at"

class ProjectStoreFilesystemMixin:
    """Workspace creation, trash, path mapping, and index maintenance."""
    def _ensure_workspace(self) -> None:
        root = self.settings.workspace_root
        root.mkdir(parents=True, exist_ok=True)
        index = root / "_index.json"
        if not index.exists():
            safe_write_json(index, {"version": 1, "projects": []})
        # 启动时顺带清理过期回收站
        self.purge_expired_trash()

    def trash_root(self) -> Path:
        path = self.workspace_root / "_trash"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def purge_expired_trash(self, *, max_days: int = TRASH_RETENTION_DAYS) -> int:
        """删除回收站中超过保留期的项目目录，返回删除数量。"""
        days = max(1, int(max_days or TRASH_RETENTION_DAYS))
        trash = self.workspace_root / "_trash"
        if not trash.exists():
            return 0
        cutoff = datetime.now().timestamp() - days * 86400
        removed = 0
        try:
            children = list(trash.iterdir())
        except OSError as e:
            logger.warning("读取回收站失败: %s", e)
            return 0
        for child in children:
            if not child.is_dir():
                continue
            try:
                trashed_at = self._trash_entry_time(child)
                if trashed_at is None or trashed_at > cutoff:
                    continue
                shutil.rmtree(child, ignore_errors=True)
                if not child.exists():
                    removed += 1
                    logger.info("已清理过期回收站项目：%s", child.name)
            except OSError as e:
                logger.warning("清理回收站项目失败 %s: %s", child, e)
        return removed

    @staticmethod
    def _trash_entry_time(entry: Path) -> float | None:
        """回收时间戳：优先读 _trashed_at，否则用目录 mtime。"""
        marker = entry / _TRASHED_AT_NAME
        if marker.exists():
            try:
                raw = marker.read_text(encoding="utf-8").strip()
                if raw.isdigit():
                    return float(raw)
                return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").timestamp()
            except (OSError, ValueError):
                pass
        try:
            return entry.stat().st_mtime
        except OSError:
            return None

    def _mark_trashed(self, dest: Path) -> None:
        try:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            (dest / _TRASHED_AT_NAME).write_text(stamp, encoding="utf-8")
        except OSError as e:
            logger.warning("写入回收时间失败 %s: %s", dest, e)

    @property
    def workspace_root(self) -> Path:
        return self.settings.workspace_root

    def path_for(self, project_id: str) -> Path:
        return project_dir(self.workspace_root, project_id)

    @staticmethod
    def _empty_dir(path: Path) -> None:
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            return
        for child in path.iterdir():
            try:
                if child.is_file() or child.is_symlink():
                    child.unlink(missing_ok=True)
                elif child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
            except OSError as e:
                logger.warning("清空 %s 失败: %s", child, e)

    def _touch_index_entry(self, project: Project) -> None:
        """只更新本项目的索引条目（标题/时间），不扫描全部项目。

        常驻路径（每事件/每次保存）不再走 _rebuild_index 的 list_projects() O(n) 读盘；
        结构性增删（create 首现/delete 移除）仍由对应方法保证索引正确。
        """
        with _META_LOCK:
            path = self.workspace_root / "_index.json"
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                data = None
            if not isinstance(data, dict):
                data = {"version": 1, "projects": []}
            projects = data.get("projects")
            if not isinstance(projects, list):
                projects = []
            entry = {"id": project.id, "title": project.title, "updated_at": project.updated_at}
            for i, e in enumerate(projects):
                if isinstance(e, dict) and e.get("id") == project.id:
                    projects[i] = entry  # 保序替换
                    break
            else:
                projects.append(entry)
            data["projects"] = projects
            try:
                safe_write_json(path, data)
            except OSError:
                logger.warning("写入索引失败: %s", project.id)

    def _rebuild_index(self) -> None:
        projects = self.list_projects()
        index = {
            "version": 1,
            "projects": [
                {"id": p.id, "title": p.title, "updated_at": p.updated_at}
                for p in projects
            ],
        }
        safe_write_json(self.workspace_root / "_index.json", index)

__all__ = ["ProjectStoreFilesystemMixin", "TRASH_RETENTION_DAYS"]
