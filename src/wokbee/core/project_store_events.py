"""ProjectStore event log and archive responsibilities."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from wokbee.core.models import ProjectEvent, ProjectStatus
from wokbee.core.paths import ARCHIVABLE_DIRS, archives_dir, ensure_project_layout, events_path

from wokbee.core.project_store_support import _META_LOCK, _EVENTS_LOCK, _event_lines_cache, _event_for_log

logger = logging.getLogger("wokbee")
MAX_ARCHIVES = 50


def _legacy_safe_write_text(path: Path, text: str) -> None:
    """保留旧模块的 mock/扩展挂点，兼容调用方对 project_store.safe_write_text 的替换。"""
    from wokbee.core import project_store

    project_store.safe_write_text(path, text)

class ProjectStoreEventsMixin:
    """Append-only event storage, archive lifecycle, and event cache."""
    def append_event(self, project_id: str, event: ProjectEvent) -> None:
        # These aliases intentionally resolve through the facade at call time:
        # legacy integrations and tests patch the original project_store module.
        from wokbee.core import project_store as legacy

        root = self.path_for(project_id)
        legacy.ensure_project_layout(root)
        path = legacy.events_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(_event_for_log(event), ensure_ascii=False) + "\n"
        if path.exists():
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
        else:
            legacy.safe_write_text(path, line)
        # 追加写入后同步行缓存（避免下次读再全量重读）
        with _EVENTS_LOCK:
            cached = _event_lines_cache.get(project_id)
            if cached is not None:
                cached.append(line.rstrip("\n"))
        # 事件流可能每秒产生大量工具事件，不要为每条事件重写 project.json 和
        # _index.json；这会与 AutoBee/UI 并发刷新形成高频原子替换。用户事件、错误、
        # 经验和生命周期终态足以更新项目时间，其余事件由后续状态/元数据保存带上。
        meta = event.meta if isinstance(event.meta, dict) else {}
        should_touch = event.kind in {"user", "error", "lesson"} or bool(
            meta.get("lifecycle") in {"started", "finished"}
        )
        if should_touch:
            with _META_LOCK:
                project = self.get(project_id)
                if project:
                    self.save(project)

    def _event_lines(self, project_id: str) -> list[str]:
        """读取项目的 events.jsonl 行列表（不做 json 解析），按 project_id 缓存。"""
        with _EVENTS_LOCK:
            cached = _event_lines_cache.get(project_id)
            if cached is not None:
                return cached
        path = events_path(self.path_for(project_id))
        lines: list[str] = []
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as f:
                    lines = [ln.rstrip("\n") for ln in f if ln.strip()]
            except OSError:
                lines = []
        with _EVENTS_LOCK:
            _event_lines_cache[project_id] = lines
        return lines

    def events_window(
        self,
        project_id: str,
        *,
        skip_from_end: int = 0,
        count: int = 50,
    ) -> tuple[list[ProjectEvent], int]:
        """从文件尾部往前取一段事件（oldest→newest），返回 (events, older_remaining)。

        skip_from_end = 已从尾部加载的事件数；count = 本次再往前取多少条。
        只对窗口内的行做 json 解析，避免每次切换项目全量解析历史。
        """
        lines = self._event_lines(project_id)
        total = len(lines)
        end = max(0, total - max(0, int(skip_from_end)))
        start = max(0, end - max(0, int(count)))
        events: list[ProjectEvent] = []
        for line in lines[start:end]:
            try:
                events.append(ProjectEvent.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
        return events, start

    def list_events(self, project_id: str, limit: int = 500) -> list[ProjectEvent]:
        lines = self._event_lines(project_id)
        if limit > 0 and len(lines) > limit:
            lines = lines[-limit:]
        events: list[ProjectEvent] = []
        for line in lines:
            try:
                events.append(ProjectEvent.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
        return events

    def clear_events(self, project_id: str) -> None:
        path = events_path(self.path_for(project_id))
        path.parent.mkdir(parents=True, exist_ok=True)
        _legacy_safe_write_text(path, "")
        with _EVENTS_LOCK:
            _event_lines_cache.pop(project_id, None)

    def needs_auto_archive(self, project_id: str) -> bool:
        """上一轮是否有可归档内容（避免首次空跑也产生空存档）。"""
        events = self.list_events(project_id)
        if any(
            e.kind in ("user", "agent", "tool", "error", "lesson", "approval")
            for e in events
        ):
            return True
        root = self.path_for(project_id)
        skip = {"archives", "scripts", "memory", "references", "uploads"}
        for name in ARCHIVABLE_DIRS:
            if name in skip:
                continue
            folder = root / name
            if not folder.exists():
                continue
            try:
                for p in folder.rglob("*"):
                    if not p.is_file():
                        continue
                    if p.name.lower() in ("readme.txt", "readme.md"):
                        continue
                    return True
            except OSError:
                continue
        return False

    def prune_archives(self, project_id: str, *, keep: int = MAX_ARCHIVES) -> int:
        """删除最旧存档，使 arch_* 目录不超过 keep 份。返回删除数量。"""
        root = archives_dir(self.path_for(project_id))
        if not root.exists():
            return 0
        archives = [
            p for p in root.iterdir()
            if p.is_dir() and p.name.startswith("arch_")
        ]
        archives.sort(key=lambda p: p.name)
        removed = 0
        while len(archives) > max(1, int(keep)):
            oldest = archives.pop(0)
            try:
                shutil.rmtree(oldest, ignore_errors=False)
                removed += 1
            except OSError as e:
                logger.warning("删除旧存档失败 %s: %s", oldest, e)
                break
        return removed

    def archive_session(
        self,
        project_id: str,
        *,
        include_memory: bool = False,
        reason: str = "manual",
    ) -> Path | None:
        """将运行记录、工作区、交付物一并归档到 archives/，并清空会话态。

        默认保留：project.json、memory/（经验）、scripts/、uploads/（含参考材料 uploads/references/）。
        include_memory=True（清空经验）时：连同 memory/ 与 scripts/ 一并归档并清空。
        不会把 archives/ 自身再归档进去；用户上传 uploads/ 始终保留。
        存档超过 MAX_ARCHIVES 份时自动删除最旧。
        """
        project = self.get(project_id)
        if not project:
            return None
        root = self.path_for(project_id)
        ensure_project_layout(root)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = archives_dir(root) / f"arch_{stamp}"
        # 同秒冲突时追加序号
        if dest.exists():
            n = 1
            while True:
                alt = archives_dir(root) / f"arch_{stamp}_{n}"
                if not alt.exists():
                    dest = alt
                    break
                n += 1
        dest.mkdir(parents=True, exist_ok=False)

        moved: list[str] = []
        for name in ARCHIVABLE_DIRS:
            # 明确跳过长期保留目录，防止误归档
            if name in ("archives", "scripts", "memory", "references", "uploads"):
                continue
            src = root / name
            if not src.exists():
                continue
            target = dest / name
            try:
                shutil.copytree(src, target, dirs_exist_ok=True)
                moved.append(name)
            except OSError as e:
                logger.warning("归档复制 %s 失败: %s", src, e)
                continue
            # 清空源目录内容（保留空目录）
            self._empty_dir(src)

        kept = ["project.json", "archives/", "uploads/", "memory/session_memory.md"]
        if include_memory:
            for extra in ("memory", "scripts"):
                src = root / extra
                if not src.exists():
                    continue
                session_copy = None
                session_path = src / "session_memory.md" if extra == "memory" else None
                if session_path and session_path.exists():
                    # Experience cleanup may archive memory/, but session history
                    # is permanent and must remain in the live project.
                    session_copy = session_path.read_bytes()
                try:
                    shutil.copytree(src, dest / extra, dirs_exist_ok=True)
                    if session_path:
                        archived_session = dest / extra / "session_memory.md"
                        if archived_session.exists():
                            archived_session.unlink()
                    moved.append(extra)
                    self._empty_dir(src)
                    if session_path and session_copy is not None:
                        session_path.parent.mkdir(parents=True, exist_ok=True)
                        session_path.write_bytes(session_copy)
                except OSError as e:
                    logger.warning("归档复制 %s 失败: %s", extra, e)
        else:
            kept.insert(1, "memory/")
            kept.insert(2, "scripts/")

        mode_line = (
            "- 模式：清空经验（含 memory/、scripts/；保留 uploads/ 与参考材料）\n"
            if include_memory
            else (
                "- 模式：运行前自动归档（保留 memory/、scripts/、uploads/）\n"
                if reason == "auto_before_run"
                else "- 模式：普通归档（保留 memory/、scripts/、uploads/）\n"
            )
        )
        manifest = (
            f"# 归档 {stamp}\n\n"
            f"- 项目：{project.title} (`{project_id}`)\n"
            f"- 目标：{project.goal or '（无）'}\n"
            f"- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"- 已归档目录：{', '.join(moved) or '（空）'}\n"
            f"- 保留未归档：{', '.join(f'`{x}`' for x in kept)}\n"
            f"{mode_line}"
        )
        _legacy_safe_write_text(dest / "MANIFEST.md", manifest)

        # 重建空布局
        ensure_project_layout(root)
        self.clear_events(project_id)

        project.status = ProjectStatus.IDLE
        project.current_step = "已归档" if not include_memory else "已清空经验"
        project.progress_done = 0
        project.progress_total = 0
        project.artifacts_summary = ""
        self.save(project)

        pruned = self.prune_archives(project_id, keep=MAX_ARCHIVES)
        prune_note = (
            f"\n已清理旧存档 {pruned} 份（最多保留 {MAX_ARCHIVES} 份）。"
            if pruned
            else ""
        )

        if include_memory:
            notice = (
                f"已归档到 `archives/{dest.name}`"
                f"（含会话目录、memory 经验与 scripts）。\n"
                "对话、工作区、交付物、经验文档、本地脚本已清空；"
                "项目名称、目标、uploads/ 上传资料与参考材料已保留。\n"
                "注意：后续 Agent 运行禁止访问 archives/。"
                f"{prune_note}"
            )
        elif reason == "auto_before_run":
            notice = (
                f"运行前已自动归档到 `archives/{dest.name}`"
                f"（保留经验、scripts/、uploads/ 与参考材料）。{prune_note}"
            )
        else:
            notice = (
                f"已归档到 `archives/{dest.name}`"
                f"（含 runs / workspace / deliverables）。\n"
                "对话与工作区、交付物已清空；"
                "目标、memory/experiences/、scripts/、uploads/ 已保留，可直接再次运行。\n"
                "注意：后续 Agent 运行禁止访问 archives/。"
                f"{prune_note}"
            )
        self.append_event(
            project_id,
            ProjectEvent(
                kind="info",
                content=notice,
                meta={
                    "archive": str(dest),
                    "include_memory": include_memory,
                    "include_scripts": include_memory,
                    "reason": reason,
                    "pruned": pruned,
                },
            ),
        )
        return dest

__all__ = ["ProjectStoreEventsMixin", "MAX_ARCHIVES"]
