"""经验文档与索引存储。"""

from __future__ import annotations

import re
from pathlib import Path

from tokbee.core.safe_io import safe_write_text

from wokbee.core.paths import ensure_project_layout, memory_dir, scripts_dir
from wokbee.engine.lesson_models import Lesson, _now, _slug, _stamp, render_lesson_md

EXPERIENCES_SUBDIR = "experiences"
LEGACY_SINGLE = "EXPERIENCE.md"
_EXP_NAME_RE = re.compile(
    r"^exp_(\d{8}_\d{6}(?:_\d{3})?)(?:_[a-z0-9]+)?\.md$", re.I
)


class LessonStore:
    """`memory/experiences/exp_YYYYMMDD_HHMMSS.md` 多份经验；运行只读最新。"""

    def __init__(self, project_root: Path):
        self.root = Path(project_root)
        ensure_project_layout(self.root)
        self.memory = memory_dir(self.root)
        self.memory.mkdir(parents=True, exist_ok=True)
        self.experiences_dir = self.memory / EXPERIENCES_SUBDIR
        self.experiences_dir.mkdir(parents=True, exist_ok=True)
        self._maybe_migrate_legacy()

    @property
    def experience_path(self) -> Path | None:
        return self.latest_path()

    @property
    def index_path(self) -> Path:
        latest = self.latest_path()
        return latest if latest else self.experiences_dir

    def _maybe_migrate_legacy(self) -> None:
        legacy_single = self.memory / LEGACY_SINGLE
        if legacy_single.exists() and legacy_single.is_file():
            if not any(self.experiences_dir.glob("exp_*.md")):
                dest = self.experiences_dir / f"exp_{_stamp()}_migrated.md"
                try:
                    safe_write_text(dest, legacy_single.read_text(encoding="utf-8"))
                except OSError:
                    pass
            try:
                bak = self.memory / "EXPERIENCE.md.bak"
                if not bak.exists():
                    legacy_single.replace(bak)
            except OSError:
                pass

        old_idx = self.memory / "EXPERIENCES.md"
        if old_idx.exists() and not any(self.experiences_dir.glob("exp_*.md")):
            try:
                safe_write_text(
                    self.experiences_dir / f"exp_{_stamp()}_index.md",
                    old_idx.read_text(encoding="utf-8"),
                )
            except OSError:
                pass

    def list_paths(self) -> list[Path]:
        files = [p for p in self.experiences_dir.glob("exp_*.md") if p.is_file()]

        def sort_key(p: Path):
            m = _EXP_NAME_RE.match(p.name)
            stamp = m.group(1) if m else ""
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0.0
            return (stamp, mtime, p.name)

        return sorted(files, key=sort_key, reverse=True)

    def list_recent(self, limit: int = 20) -> list[Path]:
        return self.list_paths()[:limit]

    def latest_path(self) -> Path | None:
        paths = self.list_paths()
        return paths[0] if paths else None

    def is_empty(self) -> bool:
        latest = self.latest_path()
        if not latest:
            return True
        try:
            text = latest.read_text(encoding="utf-8").strip()
        except OSError:
            return True
        if not text:
            return True
        if "（暂无经验" in text and "## 实现步骤" not in text and "## 成功实现路径" not in text:
            return True
        # 至少要有 front matter 或某个核心章节
        if text.startswith("---") or "## 实现步骤" in text or "## 成功实现路径" in text or "## 执行顺序" in text:
            return False
        return len(text) < 80

    def read_latest_text(self, *, max_chars: int = 0) -> str:
        path = self.latest_path()
        if not path:
            return ""
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
        if max_chars > 0 and len(text) > max_chars:
            return text[:max_chars] + "\n…(截断)"
        return text

    def save(self, lesson: Lesson) -> Path:
        """始终新建带时间戳的经验文件（不覆盖旧文件）。"""
        lesson.created_at = lesson.created_at or _now()
        lesson.updated_at = _now()
        stamp = _stamp()
        fname = f"exp_{stamp}.md"
        path = self.experiences_dir / fname
        if path.exists():
            fname = f"exp_{stamp}_{lesson.id[-4:]}.md"
            path = self.experiences_dir / fname
        lesson.filename = f"{EXPERIENCES_SUBDIR}/{fname}"
        safe_write_text(path, render_lesson_md(lesson))
        return path

    def virtual_memory_paths(self, *, recent: int = 8) -> list[str]:
        paths = ["/memory/AGENTS.md"]
        latest = self.latest_path()
        if latest:
            rel = latest.relative_to(self.memory).as_posix()
            paths.append(f"/memory/{rel}")
        return paths

    def prompt_digest(self, *, limit: int = 5, max_chars: int = 3500) -> str:
        text = self.read_latest_text(max_chars=max_chars)
        if not text:
            return ""
        latest = self.latest_path()
        name = latest.name if latest else "latest"
        return (
            f"【项目经验记忆】以下来自最新经验 `{name}`（历史经验不自动注入；"
            "只关注摘要/成功路径/注意事项，忽略任何结果或产物描述）：\n\n"
            + text
            + "\n\n经验只含**成功路径**：按每步「操作+目的」理解，忽略任何失败/试错细节。\n"
        )

    def rebuild_index(self) -> None:
        self.experiences_dir.mkdir(parents=True, exist_ok=True)

    def open_in_browser(self) -> bool:
        path = self.latest_path()
        if not path:
            return False
        try:
            import webbrowser

            webbrowser.open(path.resolve().as_uri())
            return True
        except OSError:
            return False
