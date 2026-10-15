"""压缩线程、待继续发送项和压缩轮次的唯一所有者。"""
import logging
from PySide6.QtCore import QObject
from beehive.system.memory import context as ctxman
from beehive.system.providers.ai_client import AIClient
from beehive.system.providers.model_settings import settings_for_model
from beehive.features.tokbee.ui.workers.compaction import _CompactWorker

logger = logging.getLogger("tokbee")


class ChatCompactionController(QObject):
    def __init__(self, view):
        super().__init__(view)
        self.view, self.vm = view, view.vm
        self._compact_worker = None
        self._pending_send = None
        self._compact_rounds = 0

    @property
    def busy(self):
        return self._compact_worker is not None and self._compact_worker.isRunning()

    def enqueue(self, sid, content, params):
        self._pending_send = {"sid": sid, "api_user_content": content, "params": params}
        self._compact_rounds = 0
        self.start(manual=False)

    def manual(self):
        if not self.vm.session:
            self.view.options.tip("请先开始对话")
            return
        if not self.vm.current_model or not self.vm.current_model.api_host:
            self.view.options.tip("请先配置模型后再压缩")
            return
        if self._compact_worker and self._compact_worker.isRunning():
            return
        plan = ctxman.plan_compaction(self.vm.session.messages, self.vm.session.compaction_points)
        if plan is None:
            self.view.options.tip("当前对话较短，无需压缩")
            return
        self._pending_send = None
        self.start(manual=True)

    def start(self, *, manual: bool, session=None):
        # session 缺省为当前显示会话；压缩回调期间可能切走，回调要用「发起时」的会话对象
        sess = session or self.vm.session
        if not sess:
            return
        plan = ctxman.plan_compaction(sess.messages, sess.compaction_points)
        if plan is None:
            if manual:
                self.view.options.tip("当前对话较短，无需压缩")
            elif self._pending_send:
                ps = self._pending_send
                self._pending_send = None
                self.view.requests.start(ps["sid"], ps["api_user_content"], ps["params"])
            return

        to_compact, _retained, new_boundary, prev_summary, pin_end = plan
        client = None
        if self.vm.current_model and self.vm.current_model.api_host:
            client = AIClient(
                self.vm.current_model.api_host,
                self.vm.current_model.api_key,
                self.vm.current_model.model_id,
                family=self.vm.current_model.family,
                protocol=self.vm.current_model.api_protocol,
            )
        if manual:
            self.vm.set_sending(True)
            self.view.composer.send_btn.setText("压缩中")

        worker = _CompactWorker(
            client,
            to_compact,
            prev_summary,
            new_boundary,
            parent=self,
            pin_end=pin_end,
            settings=settings_for_model(self.vm.current_model) if self.vm.current_model else None,
        )
        self._compact_worker = worker
        worker.compact_done.connect(
            lambda summary, boundary, pin, m=manual, s=sess: self.done(
                summary, boundary, m, pin_end=pin, sess=s
            )
        )
        worker.failed.connect(lambda err, m=manual: self.failed(err, m))
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self.view._refresh_context_ring()

    def done(self, summary: str, boundary: int, manual: bool, pin_end: int = 0, sess=None):
        self._compact_worker = None
        # 用「发起压缩时」的会话，而非当前显示会话（压缩期间用户可能已切换）
        session = sess or self.vm.session
        if session is None:
            self.vm.set_sending(False)
            self._pending_send = None
            return
        session.compaction_points = ctxman.append_compaction_point(
            session.compaction_points,
            summary=summary,
            boundary_index=boundary,
            pin_end=pin_end,
        )
        self.vm.manager.save()
        self.view._refresh_context_ring()
        if manual and not self._pending_send:
            self.vm.set_sending(False)
            self.view.options.tip("已压缩上下文")
            return
        if self._pending_send:
            ps = self._pending_send
            self._pending_send = None
            usage = self.view.requests.usage(include_draft=False, session=session)
            params = ps["params"]
            self._compact_rounds += 1
            if (
                self._compact_rounds < 3
                and ctxman.needs_compaction(usage, auto_compaction=params.auto_compaction)
            ):
                self._pending_send = ps
                self.start(manual=False, session=session)
            else:
                self._compact_rounds = 0
                self.view.requests.start(ps["sid"], ps["api_user_content"], params)

    def failed(self, err: str, manual: bool):
        self._compact_worker = None
        logger.error("上下文压缩失败: %s", err)
        if self._pending_send:
            # 自动压缩失败时仍尝试发送（由 token 裁剪兜底）
            ps = self._pending_send
            self._pending_send = None
            self.view.requests.start(ps["sid"], ps["api_user_content"], ps["params"])
            return
        self.vm.set_sending(False)
        if manual:
            self.view.options.tip(f"压缩失败: {err}")
        self.view._refresh_context_ring()
