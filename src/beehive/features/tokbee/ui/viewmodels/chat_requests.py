"""发送准备、上下文和聊天线程协调；不保存重复会话状态。"""
from datetime import datetime
from PySide6.QtCore import QObject
from beehive.features.tokbee.core.file_reader import is_image
from beehive.features.tokbee.ui.workers.chat import _AIChatWorker
from beehive.features.tokbee.ui.viewmodels.chat_payload import prepare_attachments, history_to_api_messages
from beehive.system.providers.model_settings import apply_model_settings
from beehive.system.providers.session_settings import SessionSettings
from beehive.system.memory import context as ctxman


class ChatRequestController(QObject):
    def __init__(self, vm, view):
        super().__init__(view)
        self.vm, self.view = vm, view
        self.workers = {}

    def send(self):
        text = self.view.composer.input_box.toPlainText().strip()
        has_files = bool(self.vm.pending_files)
        if not text and not has_files:
            return

        if not self.vm.session:
            if not self.vm.current_model:
                self.view.options.tip("请先选择模型")
                return
            session = self.vm.ensure_session()
            self.view.session_created.emit(session.id)

        if self.vm.session.messages == []:
            self.view.chat_log.clear()

        self.view._header.show()
        self.view.composer.settings_btn.show()
        self.view._title_label.setText(self.vm.session.title)
        try:
            dt = datetime.strptime(self.vm.session.updated_at, "%Y-%m-%d %H:%M:%S")
            self.view._time_label.setText(dt.strftime("%Y-%m-%d %H:%M"))
        except ValueError:
            self.view._time_label.setText(self.vm.session.updated_at)

        self.view.composer.input_box.clear()
        self.view.composer.input_box.setPlaceholderText("输入消息...")

        sid = self.vm.session.id
        api_user_content, saved_attachments = prepare_attachments(
            self.vm.pending_files, sid, text, self.view.options.tip,
        )
        self.vm.clear_files()

        display_text = text
        if not display_text.strip() and saved_attachments:
            display_text = ""

        # 无模型时先提示再返回：不持久化用户消息、不显示气泡，避免「孤儿提问」（A3）
        if not self.vm.current_model or not self.vm.current_model.api_host:
            self.view.options.tip("请先在「AI配置 → 厂商设置」中配置模型的 API 地址")
            return

        user_msg: dict = {"role": "user", "content": display_text}
        if saved_attachments:
            user_msg["attachments"] = saved_attachments
        self.vm.session.messages.append(user_msg)
        self.vm.manager.touch(self.vm.session.id)
        self.view.naming.start(self.vm.session)
        self.vm.manager.save()

        self.view.chat_log.append_message(user_msg)

        params = self.vm.session.get_params()
        params.provider = self.vm.current_model.provider_id
        params.model_id = self.vm.current_model.model_id
        apply_model_settings(self.vm.current_model, params)
        self.vm.session.set_params(params)
        self.vm.set_sending(True)

        usage = self.usage(include_draft=False)
        if ctxman.needs_compaction(usage, auto_compaction=params.auto_compaction):
            self.view.compaction.enqueue(sid, api_user_content, params)
            return

        self.start(sid, api_user_content, params)

    def cancel(self, session_id: str):
        """取消指定 session 正在运行的 worker，保存已收到的流式内容。"""
        worker = self.workers.get(session_id)
        if worker is None:
            return
        if worker.isRunning():
            session = self.vm.manager.get(session_id)
            if session and worker.accumulated_content:
                msg: dict = {"role": "assistant", "content": worker.accumulated_content}
                if worker.accumulated_reasoning:
                    msg["reasoning_content"] = worker.accumulated_reasoning
                session.messages.append(msg)
                self.vm.manager.save()

            worker.cancel()
            worker.wait(3000)

        self.workers.pop(session_id, None)

    def assemble(self, api_user_content, params: SessionSettings, session=None) -> list[dict]:
        # 按传入会话组装，缺省用当前显示会话。压缩/发送回调必须用「发起时」的会话，
        # 不能用 self.vm.session —— 否则压缩 worker 运行期间切到别的会话，会用错上下文。
        sess = session or self.vm.session
        assert sess is not None
        ctx_window = 0
        max_out = params.max_tokens
        if self.vm.current_model:
            ctx_window = int(self.vm.current_model.context_window or 0)
        summary, hist = ctxman.build_context_message_dicts(
            messages=sess.messages,
            compaction_points=sess.compaction_points,
            system_prompt=params.system_prompt,
            max_context_message_count=params.max_context_message_count,
            context_window=ctx_window,
            max_output=max_out,
            exclude_last=True,
        )
        api_messages = history_to_api_messages(hist)
        api_messages.append({"role": "user", "content": api_user_content})
        # system + summary 插在最前（摘要紧随 system）
        head: list[dict] = []
        if params.system_prompt.strip():
            head.append({"role": "system", "content": params.system_prompt.strip()})
        if summary.strip():
            head.append(ctxman.summary_as_message(summary))
        return head + api_messages

    def start(self, sid: str, api_user_content, params: SessionSettings):
        if not self.vm.current_model:
            self.vm.set_sending(False)
            return
        # 历史必须按该 sid 对应会话组装，而非当前显示会话（压缩/异步回调期间可能已切会话）
        session = self.vm.manager.get(sid) or self.vm.session
        api_messages = self.assemble(api_user_content, params, session=session)
        use_stream = params.stream
        self.cancel(sid)

        if use_stream:
            self.view.stream.begin()

        worker = _AIChatWorker(
            session_id=sid,
            model=self.vm.current_model,
            messages=api_messages,
            settings=params,
            parent=self,
        )
        self.workers[sid] = worker
        worker.chunk_received.connect(self.view.stream.chunk)
        worker.stream_done.connect(self.view.stream.finished)
        worker.non_stream_done.connect(self.view.stream.sync_finished)
        worker.error.connect(self.view.stream.failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self.view._refresh_context_ring()

    def usage(self, *, include_draft: bool = True, session=None) -> ctxman.ContextUsage:
        # 传 session 时为「非当前显示会话」计算（如压缩回调对发起会话做决策），
        # 此时输入框草稿/待附图片属当前视图，不应混入其它会话。
        sess = session or self.vm.session
        visible = sess is self.vm.session
        params = sess.get_params() if sess else SessionSettings()
        ctx_window = int(self.vm.current_model.context_window or 0) if self.vm.current_model else 0
        draft = self.view.composer.input_box.toPlainText() if (include_draft and visible) else ""
        pending_imgs = (sum(1 for p in self.vm.pending_files if is_image(p))) if visible else 0
        return ctxman.estimate_session_usage(
            messages=sess.messages if sess else [],
            compaction_points=sess.compaction_points if sess else [],
            system_prompt=params.system_prompt,
            max_context_message_count=params.max_context_message_count,
            context_window=ctx_window,
            compaction_threshold=params.compaction_threshold,
            max_output=params.max_tokens,
            draft_text=draft,
            pending_image_count=pending_imgs,
        )
