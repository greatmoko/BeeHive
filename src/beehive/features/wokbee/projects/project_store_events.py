"""项目事件追加、历史窗口与缓存（memory/events.jsonl，轮次 schema）。"""
from __future__ import annotations
import json
from beehive.features.wokbee.projects.models import ProjectEvent
from beehive.features.wokbee.projects.paths import ensure_project_layout, events_path
from beehive.features.wokbee.projects.project_store_support import _META_LOCK, _EVENTS_LOCK, _event_lines_cache
from beehive.features.wokbee.projects.round_events import build_event_payload, parse_event_payload
from beehive.common.io.safe_io import safe_write_text
from beehive.features.wokbee.projects.project_archive import needs_auto_archive, prune_archives, archive_session, MAX_ARCHIVES

class ProjectStoreEventsMixin:
    def append_event(self, project_id: str, event: ProjectEvent) -> None:

        root = self.path_for(project_id)
        ensure_project_layout(root)
        path = events_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = build_event_payload(project_id, event, path)
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        if path.exists():
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
        else:
            safe_write_text(path, line)
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
                events.append(parse_event_payload(json.loads(line)))
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
                events.append(parse_event_payload(json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
        return events

    def clear_events(self, project_id: str) -> None:
        path = events_path(self.path_for(project_id))
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_write_text(path, "")
        with _EVENTS_LOCK:
            _event_lines_cache.pop(project_id, None)
        from beehive.features.wokbee.projects.round_events import end_round
        end_round(project_id)

    def needs_auto_archive(self, project_id: str):
        return needs_auto_archive(self, project_id)

    def prune_archives(self, project_id: str, *, keep: int = MAX_ARCHIVES):
        return prune_archives(self, project_id, keep=keep)

    def archive_session(self, project_id: str, *, include_memory: bool = False, reason: str = "manual"):
        return archive_session(self, project_id, include_memory=include_memory, reason=reason)
