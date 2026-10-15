"""项目归档判断、快照与历史清理（目录契约 v2）。

v2 项目根目录契约（见 paths.py）：archives/、uploads/、output/、memory/、.project/。
- 普通归档：复制 output/ 全部内容 + 项目根目录本轮散落的普通文件，随后清空
  （output/ 保留 README.txt）；uploads/、memory/（含 events.jsonl）、.project/、
  archives/ 自身、旧版目录（runs/、workspace/、scripts/ 等，如存在）一律不动。
- include_memory=True（清空经验归档）：在普通归档基础上，把 memory/ 下的
  经验（exp_*.md，含 memory/experiences/ 旧布局）、pipeline.json、复用脚本、
  技能快照目录、skills_manifest.md 一并归档并清除；events.jsonl 永久保留、
  绝不清空，旧项目的 memory/session_memory.md 保留不清不归档。
- 事件日志永久保留：归档不再调用 clear_events。
"""
from __future__ import annotations
import logging
import shutil
from datetime import datetime
from pathlib import Path

from beehive.features.wokbee.projects.models import ProjectEvent, ProjectStatus
from beehive.features.wokbee.projects.paths import ARCHIVABLE_DIRS, archives_dir, ensure_project_layout
from beehive.common.io.safe_io import safe_write_text

logger = logging.getLogger("wokbee")
MAX_ARCHIVES = 50

# 说明文件（大小写不敏感）：不参与归档判断，也不随散文件归档
_README_NAMES = {"readme.txt", "readme.md"}
# 项目根目录固定不归档的文件（目录项、隐藏文件另行排除）
_ROOT_KEEP_FILES = _README_NAMES | {"project.json"}
# memory/ 中永远保留、绝不清空不归档的文件
_MEMORY_KEEP_FILES = {"events.jsonl", "session_memory.md"}

_REASON_LABELS = {
    "manual": "手动归档",
    "auto_before_run": "运行前自动归档",
    "archive_manual": "手动归档",
}


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def _root_scattered_files(root: Path) -> list[Path]:
    """项目根目录下本轮散落的普通文件（排除说明文件、project.json、隐藏文件）。"""
    files: list[Path] = []
    try:
        children = sorted(root.iterdir())
    except OSError as e:
        logger.warning("读取项目根目录失败 %s: %s", root, e)
        return []
    for p in children:
        if p.is_symlink() or not p.is_file():
            continue
        if p.name.lower() in _ROOT_KEEP_FILES or _is_hidden(p.name):
            continue
        files.append(p)
    return files


def _copy_tree_tracked(
    src: Path,
    dst: Path,
    root: Path,
    archived: list[str],
    failed: list[tuple[str, str]],
) -> list[Path]:
    """把 src 目录内容逐文件复制到 dst（保留相对路径），记录成功/失败。

    返回成功复制的源文件路径；失败的源文件原地保留不动。
    """
    copied: list[Path] = []
    try:
        entries = sorted(src.rglob("*"))
    except OSError as e:
        failed.append((src.relative_to(root).as_posix() + "/", str(e)))
        return []
    for p in entries:
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            # 不跟随符号链接，保留原样
            failed.append((rel, "符号链接，跳过（保留原样）"))
            continue
        if not p.is_file():
            continue
        try:
            target = dst / p.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            archived.append(rel)
            copied.append(p)
        except OSError as e:
            failed.append((rel, str(e)))
    return copied


def _delete_copied(copied: list[Path], root: Path, failed: list[tuple[str, str]]) -> None:
    """删除已成功复制的源文件；删除失败的源文件保留并记录。"""
    for p in copied:
        try:
            p.unlink()
        except OSError as e:
            failed.append((p.relative_to(root).as_posix(), f"清理失败（源文件保留）: {e}"))


def _prune_empty_dirs(src: Path) -> None:
    """自底向上删除 src 下的空子目录（不动 src 自身）。"""
    if not src.is_dir():
        return
    try:
        subdirs = [p for p in src.rglob("*") if p.is_dir() and not p.is_symlink()]
    except OSError:
        return
    for d in sorted(subdirs, key=lambda p: len(p.parts), reverse=True):
        if d == src:
            continue
        try:
            next(d.iterdir())
        except StopIteration:
            try:
                d.rmdir()
            except OSError:
                pass
        except OSError:
            pass


def needs_auto_archive(self, project_id: str) -> bool:
    """上一轮是否有可归档内容（output/ 除 README 外有内容，或根目录有散落普通文件）。

    旧版目录（runs/、workspace/、scripts/、deliverables/ 等）不参与判断，
    避免对旧项目产生意外归档清理。
    """
    root = self.path_for(project_id)
    for name in ARCHIVABLE_DIRS:  # v2 契约下为 ("output",)
        folder = root / name
        if not folder.exists():
            continue
        try:
            for p in folder.rglob("*"):
                if p.is_file() and p.name.lower() not in _README_NAMES:
                    return True
        except OSError:
            continue
    return bool(_root_scattered_files(root))


def prune_archives(self, project_id: str, *, keep: int = MAX_ARCHIVES) -> int:
    """删除最旧存档，使 arch_* 目录不超过 keep 份（discard_* 不受影响）。返回删除数量。"""
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


def _archive_output(root: Path, dest: Path, archived: list[str], failed: list[tuple[str, str]]) -> None:
    """复制 output/ 全部内容到快照 output/，随后清空已成功复制的源文件。

    output/ 目录自身保留；README.txt 由 ensure_project_layout 重建；复制失败的源文件保留。
    """
    src = root / "output"
    if not src.is_dir():
        return
    copied = _copy_tree_tracked(src, dest / "output", root, archived, failed)
    _delete_copied(copied, root, failed)
    _prune_empty_dirs(src)


def _archive_root_files(root: Path, dest: Path, archived: list[str], failed: list[tuple[str, str]]) -> None:
    """把根目录本轮散落的普通文件复制到快照根目录（保留文件名），成功后删除源文件。"""
    for p in _root_scattered_files(root):
        rel = p.name
        try:
            shutil.copy2(p, dest / p.name)
            archived.append(rel)
        except OSError as e:
            failed.append((rel, str(e)))
            continue
        try:
            p.unlink()
        except OSError as e:
            failed.append((rel, f"清理失败（源文件保留）: {e}"))


def _archive_memory_experience(root: Path, dest: Path, archived: list[str], failed: list[tuple[str, str]]) -> None:
    """清空经验归档：复制并清除 memory/ 下的经验内容（events.jsonl、session_memory.md 除外）。

    - 子目录（如 memory/experiences/ 旧布局、技能快照目录）整目录复制后清除；
    - 顶层文件除 events.jsonl / session_memory.md 外（exp_*.md、pipeline.json、
      复用脚本、skills_manifest.md 等）逐个复制后删除；
    - 复制失败的源文件原地保留。
    """
    mem = root / "memory"
    if not mem.is_dir():
        return
    dst = dest / "memory"
    try:
        children = sorted(mem.iterdir())
    except OSError as e:
        failed.append(("memory/", str(e)))
        return
    copied: list[Path] = []
    for p in children:
        if p.is_symlink():
            failed.append((p.relative_to(root).as_posix(), "符号链接，跳过（保留原样）"))
            continue
        if p.is_dir():
            copied.extend(_copy_tree_tracked(p, dst / p.name, root, archived, failed))
        elif p.is_file() and p.name.lower() not in _MEMORY_KEEP_FILES:
            rel = p.relative_to(root).as_posix()
            try:
                (dst / p.name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst / p.name)
                archived.append(rel)
                copied.append(p)
            except OSError as e:
                failed.append((rel, str(e)))
    _delete_copied(copied, root, failed)
    _prune_empty_dirs(mem)


def _write_manifest(
    dest: Path,
    project,
    project_id: str,
    reason: str,
    include_memory: bool,
    stamp: str,
    archived: list[str],
    failed: list[tuple[str, str]],
) -> None:
    reason_label = _REASON_LABELS.get(reason, reason)
    if include_memory:
        mode = "清空经验归档（memory/ 中经验、管线与复用脚本一并归档清空；events.jsonl 事件日志永久保留）"
    else:
        mode = "普通归档（memory/、uploads/、.project/ 全部保留）"
    lines = [
        f"# 归档 {stamp}",
        "",
        f"- 项目：{project.title} (`{project_id}`)",
        f"- 目标：{project.goal or '（无）'}",
        f"- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 原因：{reason_label}",
        f"- 模式：{mode}",
        "",
        "## 归档清单",
    ]
    if archived:
        lines.extend(f"- `{item}`" for item in archived)
    else:
        lines.append("- （空）")
    lines.extend([
        "",
        "## 保留项",
        "- `uploads/`（用户上传资料，长期保留）",
        "- `memory/events.jsonl`（事件日志，永久保留，绝不清空）",
    ])
    if include_memory:
        lines.append("- `memory/session_memory.md`（旧版会话记忆，如存在，保留不清不归档）")
        lines.append("- `memory/` 中其余未归档内容（如旧版 memory/experiences/ 复制失败时原地保留）")
    else:
        lines.append("- `memory/` 全部内容（含经验 exp_*.md、pipeline.json、复用脚本、技能快照）")
    lines.extend([
        "- `.project/`（project.json 与 Agent.md）",
        "- `archives/` 自身（Agent 禁止访问）",
        "- 旧版目录（runs/、workspace/、scripts/、deliverables/ 等，如存在）保持原样",
        "",
        "## 复制失败项（源文件保留未删）",
    ])
    if failed:
        lines.extend(f"- `{path}`：{err}" for path, err in failed)
    else:
        lines.append("- （无）")
    safe_write_text(dest / "MANIFEST.md", "\n".join(lines) + "\n")


def archive_session(
    self,
    project_id: str,
    *,
    include_memory: bool = False,
    reason: str = "manual",
) -> Path | None:
    """把本轮交付物与散落材料归档到 archives/arch_时间戳/，并重置会话状态。

        普通归档：复制 output/ 全部内容到快照 output/ 子目录；复制项目根目录本轮
        散落的普通文件（临时脚本、参考文件、中间材料、script_callback_*.md 等）到
        快照根目录；随后清空 output/（保留 README.txt）与已成功复制的散文件。
        永远保留：uploads/、memory/（全部内容，含 events.jsonl）、.project/、
        archives/ 自身；旧版目录（runs/、workspace/、scripts/ 等）不动。
        include_memory=True（清空经验）：另把 memory/ 下经验（exp_*.md）、
        pipeline.json、复用脚本、技能快照目录、skills_manifest.md 归档并清除，
        events.jsonl 永久保留；不再调用 clear_events。
        复制失败的源文件保留不删，并记录在快照 MANIFEST.md 中。
        存档超过 MAX_ARCHIVES 份时自动删除最旧（仅 arch_*，不动 discard_*）。
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

    archived: list[str] = []
    failed: list[tuple[str, str]] = []
    # 1) output/ 全部内容 → 快照 output/，随后清空（README.txt 保留）
    _archive_output(root, dest, archived, failed)
    # 2) 根目录本轮散落的普通文件 → 快照根目录，随后删除
    _archive_root_files(root, dest, archived, failed)
    # 3) include_memory=True：memory/ 经验内容 → 快照 memory/，随后清除
    if include_memory:
        _archive_memory_experience(root, dest, archived, failed)
    # 4) MANIFEST.md（归档清单、保留项、失败项）
    _write_manifest(dest, project, project_id, reason, include_memory, stamp, archived, failed)

    # 重建空布局（含 output/README.txt）
    ensure_project_layout(root)

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

    prefix = "运行前已自动归档" if reason == "auto_before_run" else "已归档"
    if include_memory:
        notice = (
            f"{prefix}到 `archives/{dest.name}`，本轮材料与交付物已归档。\n"
            "memory/ 中的经验、管线与复用脚本已归档清空；"
            "事件日志 events.jsonl 永久保留；"
            "uploads/、.project/ 与项目名称、目标已保留。\n"
            "注意：后续 Agent 运行禁止访问 archives/。"
            f"{prune_note}"
        )
    else:
        notice = (
            f"{prefix}到 `archives/{dest.name}`，本轮材料与交付物已归档。\n"
            "output/ 交付物与根目录临时材料已清空；"
            "uploads/、memory/（含事件日志与经验）、.project/ 已保留，可直接再次运行。\n"
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
                "reason": reason,
                "pruned": pruned,
            },
        ),
    )
    return dest
