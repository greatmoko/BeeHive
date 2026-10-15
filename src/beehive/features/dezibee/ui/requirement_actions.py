"""需求操作。"""
from __future__ import annotations
import logging
from pathlib import Path
from PySide6.QtWidgets import QDialog
from PySide6.QtCore import QObject
from beehive.features.dezibee.ui.requirement_dialog import NewReqDialog

logger = logging.getLogger("dezibee")

class RequirementActions(QObject):
    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def _on_pin_req(self, req_id: str):
        req = self.view._store.get(req_id)
        if req is None:
            return
        req.pinned = not req.pinned
        self.view._persist(req)
        self.view._refresh()

    def _on_rename_req(self, req_id: str):
        req = self.view._store.get(req_id)
        if req is None:
            return
        from beehive.common.ui.dialogs.text_input import _ask_text

        name = _ask_text(
            self.view, self.view.theme, "重命名需求", "新的需求名称：", req.title, max_length=80
        )
        if not name or name == req.title:
            return
        req.title = name
        self.view._persist(req)
        self.view._refresh()
        if self.view.sidebar.current_selected() == req_id:
            self.view.workspace.set_req(self.view._store.get(req_id))

    def _on_copy_req_id(self, req_id: str):
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(req_id)
        from beehive.common.ui.dialogs.message import show_tip as tip

        tip(self.view, self.view.theme, f"已复制需求ID：{req_id}")

    def _on_delete_req(self, req_id: str):
        if self.view.execution._running_worker(req_id) is not None:
            from beehive.common.ui.dialogs.message import show_tip as tip

            tip(self.view, self.view.theme, "该需求正在处理中，请先暂停交互再删除需求。")
            return
        req = self.view._store.get(req_id)
        title = req.title if req else req_id
        from beehive.common.ui.dialogs.message import _confirm

        if not _confirm(
            self.view,
            self.view.theme,
            "删除需求",
            f"确定删除需求「{title}」吗？\n"
            "该需求的工作文件夹（含 Demo / PRD / 对话记录）将被一并删除。",
        ):
            return
        self.view._store.delete(req_id)
        if self.view.sidebar.current_selected() == req_id:
            self.view.sidebar.clear_selection()
            self.view.workspace.set_req(None)
        self.view._refresh()
        self.view.sidebar.select_first()

    def _on_new_req(self):
        dlg = NewReqDialog(self.view.theme, self.view)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        title = dlg.result_data()
        if not title:
            return  # 名称空时创建按钮已禁用，此处兜底
        try:
            req = self.view._store.create(title=title)
        except Exception as e:
            from beehive.common.ui.dialogs.message import show_tip as tip

            tip(self.view, self.view.theme, f"创建需求失败：{e}")
            return
        self.view._refresh()
        self.view.sidebar.select(req.id)

    def _on_preview(self):
        req = self.view._current_req()
        if req is None:
            return
        from beehive.features.dezibee.core.preview import open_in_browser, req_url

        url = req_url(req.id)
        if not url:
            from beehive.common.ui.dialogs.message import show_tip as tip

            tip(self.view, self.view.theme, "预览服务启动失败。")
            return
        ok = open_in_browser(url)
        if not ok:
            from beehive.common.ui.dialogs.message import show_tip as tip

            tip(self.view, self.view.theme, f"无法打开浏览器。请手动访问：{url}")

    def _on_export(self):
        req = self.view._current_req()
        if req is None:
            from beehive.common.ui.dialogs.message import show_tip as tip

            tip(self.view, self.view.theme, "请先选择或创建一个需求。")
            return
        index = req.index_file
        if not index.is_file():
            from beehive.common.ui.dialogs.message import show_tip as tip

            tip(self.view, self.view.theme, "当前需求还没有 Demo 原型（demo/index.html），暂时无法导出。")
            return
        from beehive.features.dezibee.core.packager import build_single_file

        # 先让用户选择保存位置，再打包写入（不默认塞进 demo/）
        from PySide6.QtWidgets import QFileDialog

        default_path = str(req.root / f"{req.id}.html")
        chosen, _ = QFileDialog.getSaveFileName(
            self.view, "导出原型", default_path, "HTML 文件 (*.html)"
        )
        if not chosen:
            return  # 用户取消

        try:
            html, warnings = build_single_file(req.demo_dir)
        except Exception as e:  # noqa: BLE001
            from beehive.common.ui.dialogs.message import show_tip as tip

            tip(self.view, self.view.theme, f"导出失败：{e}")
            return

        out = Path(chosen)
        if out.suffix.lower() != ".html":
            out = out.with_suffix(".html")
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html, encoding="utf-8")
        except OSError as e:
            from beehive.common.ui.dialogs.message import show_tip as tip

            tip(self.view, self.view.theme, f"写入导出文件失败：{e}")
            return

        from beehive.common.ui.dialogs.message import show_tip as tip

        mb = len(html.encode("utf-8")) / 1024 / 1024
        warn = ("\n\n注意：以下引用未内联（可能仍是外链）：\n" + "；".join(warnings)) if warnings else ""
        tip(
            self.view,
            self.view.theme,
            f"已导出原型（{mb:.2f} MB）：\n{out}\n\n"
            f"该文件自包含、零外部请求，直接上传 OSS 即可。{warn}",
        )

    def _on_open_folder(self):
        req = self.view._current_req()
        if req is None:
            from beehive.common.ui.dialogs.message import show_tip as tip

            tip(self.view, self.view.theme, "请先选择或创建一个需求。")
            return
        from beehive.common.process.desktop import open_path

        req_dir = self.view._store.req_dir(req.id)
        req_dir.mkdir(parents=True, exist_ok=True)
        open_path(req_dir)
