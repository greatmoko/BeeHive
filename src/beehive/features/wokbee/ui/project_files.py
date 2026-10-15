"""项目附件、交付物与归档；工作区显式持有项目与线程状态。"""
from __future__ import annotations
import logging
from pathlib import Path
from PySide6.QtWidgets import QFileDialog
from beehive.features.wokbee.projects.models import ProjectEvent
from beehive.features.wokbee.projects.paths import deliverables_dir, uploads_dir
from beehive.features.wokbee.projects.project_store import MAX_ARCHIVES
from beehive.common.ui.dialogs.message import _confirm
from beehive.common.process.desktop import open_path as _open_in_explorer
from beehive.common.ui.dialogs.message import show_tip as _tip
from beehive.features.wokbee.ui.timeline_events import INITIAL_RENDER
from PySide6.QtCore import QObject

logger = logging.getLogger("wokbee")

class ProjectFilesController(QObject):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace

    def _on_open_folder(self):
        workspace = self.workspace
        if not workspace._project_id:
            _tip(workspace, workspace.theme, "请先选择项目。")
            return
        _open_in_explorer(workspace.store.path_for(workspace._project_id))

    def _on_open_deliverables(self, project_id: str = "", _idx: int = 0):
        workspace = self.workspace
        target = project_id or workspace._project_id
        if not target:
            _tip(workspace, workspace.theme, "请先选择项目。")
            return
        path = deliverables_dir(workspace.store.path_for(target))
        path.mkdir(parents=True, exist_ok=True)
        _open_in_explorer(path)

    def _append_deliverables_bubble(self, target: str):
        """任务完成后追加一个「打开交付物目录」的快捷链接气泡（交互/运行模式）。"""
        workspace = self.workspace
        if not target:
            return
        ev = ProjectEvent(
            kind="deliverables",
            content="交付物目录",
            meta={"project_id": target},
        )
        workspace.store.append_event(target, ev)
        if target == workspace._project_id and workspace._timeline is not None:
            workspace._timeline.append_event(ev)

    def _on_upload(self):
        workspace = self.workspace
        if not workspace._project_id:
            _tip(workspace, workspace.theme, "请先选择项目。")
            return
        files, _ = QFileDialog.getOpenFileNames(
            workspace,
            "选择要上传的文件",
            "",
            "所有文件 (*.*)",
        )
        if not files:
            return
        dest_dir = uploads_dir(workspace.store.path_for(workspace._project_id))
        dest_dir.mkdir(parents=True, exist_ok=True)
        import shutil
        from datetime import datetime as _dt

        saved: list[str] = []
        for src in files:
            src_path = Path(src)
            name = src_path.name
            target = dest_dir / name
            if target.exists():
                stem, suf = src_path.stem, src_path.suffix
                stamp = _dt.now().strftime("%H%M%S")
                target = dest_dir / f"{stem}_{stamp}{suf}"
            try:
                shutil.copy2(src_path, target)
                saved.append(target.name)
            except OSError as e:
                _tip(workspace, workspace.theme, f"上传失败：{name}\n{e}")
                return
        ev = ProjectEvent(
            kind="info",
            content=(
                f"已上传 {len(saved)} 个文件到 uploads/：\n"
                + "\n".join(f"- `{n}`" for n in saved)
                + "\nAgent 运行时可直接读取调用；归档时保留上传资料，不会清空。"
            ),
        )
        workspace.store.append_event(workspace._project_id, ev)
        workspace._timeline.append_event(ev)
        workspace._schedule_essentials_refresh()
        _tip(
            workspace,
            workspace.theme,
            f"已保存到 uploads/：\n" + "\n".join(saved),
            title="上传完成",
        )

    def _auto_archive_before_run(self) -> None:
        """运行前自动归档上一轮会话（无内容则跳过）。

        当前运行不再自动调用（省 token 以便重复运行复用经验）；保留此方法供外部按需触发。
        """
        workspace = self.workspace
        if not workspace._project_id:
            return
        if not workspace.store.needs_auto_archive(workspace._project_id):
            return
        dest = workspace.store.archive_session(
            workspace._project_id,
            include_memory=False,
            reason="auto_before_run",
        )
        if not dest:
            return
        # 归档会清空时间线并写入一条提示，整表刷新到最新
        events, remaining = workspace.store.events_window(
            workspace._project_id, skip_from_end=0, count=INITIAL_RENDER
        )
        workspace._timeline.render_events(
            events, older_remaining=remaining,
            loader=workspace._make_events_loader(workspace._project_id),
        )
        workspace._refresh_essentials()
        workspace.status_changed.emit()

    def _on_archive(self):
        """归档本轮会话：交付物与根目录材料入档后清空，记忆与上传长期保留。"""
        workspace = self.workspace
        if not workspace._project_id:
            return
        if workspace._worker and workspace._worker.isRunning():
            return
        if workspace._lesson_worker and workspace._lesson_worker.isRunning():
            return
        ok = _confirm(
            workspace,
            workspace.theme,
            "归档",
            "确认归档本轮会话？\n"
            "• 归档：output/ 交付物 + 根目录本轮临时脚本/中间材料（归档后清空）\n"
            "• 保留：uploads/ 上传资料、memory/（事件日志与经验）、项目名称、目标、审核策略\n"
            f"• 每个项目最多保留 {MAX_ARCHIVES} 份存档，超出自动删除最旧的\n"
            "• 归档快照存放在 archives/，Agent 禁止访问\n\n"
            "是否继续？",
        )
        if not ok:
            return
        dest = workspace.store.archive_session(
            workspace._project_id,
            include_memory=False,
            reason="archive_manual",
        )
        if not dest:
            return
        workspace.files._archive_old_experiences(workspace._project_id, dest)
        workspace._refresh()

    def _archive_old_experiences(self, project_id: str, dest: Path) -> None:
        """把 memory/ 下除最新一份外的历史经验归档到 dest，只保留最新。"""
        workspace = self.workspace
        try:
            import shutil

            from beehive.features.wokbee.experience.lesson_storage import LessonStore

            store = LessonStore(workspace.store.path_for(project_id))
            paths = store.list_paths()
            if len(paths) <= 1:
                return
            target_root = dest / "memory"
            target_root.mkdir(parents=True, exist_ok=True)
            moved = 0
            for p in paths[1:]:
                try:
                    shutil.copy2(p, target_root / p.name)
                    p.unlink(missing_ok=True)
                    moved += 1
                except OSError as e:
                    logger.warning("归档历史经验 %s 失败: %s", p, e)
            if moved:
                latest = paths[0].name
                ev = ProjectEvent(
                    kind="info",
                    content=(
                        f"已归档 {moved} 份历史经验到 `{dest.name}/memory/`；"
                        f"仅保留最新一份 `{latest}` 供复用。"
                    ),
                )
                workspace.store.append_event(project_id, ev)
        except Exception:
            logger.exception("归档历史经验失败（会话已归档，不影响使用）")
