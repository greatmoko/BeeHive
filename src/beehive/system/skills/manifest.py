"""按职责组织的 manifest。"""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from beehive.system.settings.config import default_data_dir
_FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class SkillInfo:
    name: str
    path: Path
    description: str = ""
    enabled: bool = True
    root: Path = None  # type: ignore[assignment]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.path),
            "description": self.description,
            "enabled": self.enabled,
            "root": str(self.root) if self.root else "",
        }


def _parse_skill_md(path: Path) -> tuple[str, str]:
    """返回 (name, description)。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return path.parent.name, ""
    name = path.parent.name
    desc = ""
    m = _FRONT_MATTER.match(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k, v = k.strip().lower(), v.strip().strip("\"'")
            if k == "name" and v:
                name = v
            elif k in ("description", "desc") and v:
                desc = v
    if not desc:
        # 取正文第一行非空
        body = _FRONT_MATTER.sub("", text).strip()
        for line in body.splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                desc = line[:120]
                break
    return name, desc


def default_skills_root() -> Path:
    return default_data_dir() / "skills"
