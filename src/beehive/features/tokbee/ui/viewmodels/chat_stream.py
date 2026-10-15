"""流式缓存、思考标签与完成/失败转换的唯一所有者（渲染交给网页聊天组件）。"""
from PySide6.QtCore import QObject, QTimer

from beehive.features.wokbee.projects.models import ProjectEvent
from beehive.features.tokbee.ui.viewmodels.chat_viewmodel import ChatViewModel


def _final_event(content: str, phase: str) -> ProjectEvent:
    return ProjectEvent(kind="agent", content=content, created_at="", meta={"phase": phase})


class ChatStreamController(QObject):
    def __init__(self, view):
        super().__init__(view)
        self.view, self.vm = view, view.vm
        self.content = self.reasoning = self.buffer = ""
        self.phase = "idle"
        self.in_think = False
        self.pending_reasoning = ""  # 距上次 flush 待推送的增量
        self.pending_content = ""
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(60)
        self.timer.timeout.connect(self.flush)

    # ── 生命周期 ─────────────────────────────────────────────

    def begin(self):
        self.detach()
        self.content = self.reasoning = self.buffer = ""
        self.phase = "idle"
        self.in_think = False
        self.view.chat_log.begin_run()

    def restore(self, worker):
        """切回正在生成的会话：把已积累内容一次性灌入网页流式行。"""
        self.begin()
        self.content, self.reasoning = worker.accumulated_content, worker.accumulated_reasoning
        self.phase = "content" if self.content else ("reasoning" if self.reasoning else "idle")
        if self.reasoning:
            self.view.chat_log.append_stream("reasoning", self.reasoning)
        if self.content:
            self.view.chat_log.append_stream("text", self.content)
        self.vm.set_sending(True)

    def detach(self):
        self.timer.stop()
        self.buffer = ""
        self.pending_reasoning = self.pending_content = ""
        self.in_think = False

    # ── chunk 解析（支持 reasoning_content 字段与 <think> 标签两种思考模式） ──

    def drain_buffer(self):
        if self.buffer:
            if self.in_think:
                self.reasoning_chunk(self.buffer)
            else:
                self.content_chunk(self.buffer)
            self.buffer = ""

    def chunk(self, worker_sid: str, content_delta: str, reasoning_delta: str):
        if not self.vm.session or self.vm.session.id != worker_sid:
            return
        if reasoning_delta:
            self.reasoning_chunk(reasoning_delta)

        if content_delta:
            self.buffer += content_delta

            while self.buffer:
                if self.in_think:
                    end_pos = self.buffer.find("</think>")
                    if end_pos != -1:
                        self.reasoning_chunk(self.buffer[:end_pos])
                        self.buffer = self.buffer[end_pos + len("</think>"):]
                        self.in_think = False
                        if self.phase == "reasoning":
                            self.phase = "content"
                    else:
                        hold = len("</think>") - 1
                        if len(self.buffer) > hold:
                            safe = self.buffer[:-hold]
                            self.reasoning_chunk(safe)
                            self.buffer = self.buffer[len(safe):]
                        break
                else:
                    start_pos = self.buffer.find("<think>")
                    if start_pos != -1:
                        if start_pos > 0:
                            self.content_chunk(self.buffer[:start_pos])
                        self.buffer = self.buffer[start_pos + len("<think>"):]
                        self.in_think = True
                    elif "<" in self.buffer:
                        idx = self.buffer.rfind("<")
                        tail = self.buffer[idx:]
                        if "<think>".startswith(tail):
                            if idx > 0:
                                self.content_chunk(self.buffer[:idx])
                            self.buffer = tail
                            break
                        else:
                            self.content_chunk(self.buffer)
                            self.buffer = ""
                    else:
                        self.content_chunk(self.buffer)
                        self.buffer = ""

    def schedule(self):
        if not self.timer.isActive():
            self.timer.start()

    def reasoning_chunk(self, text: str):
        if not text:
            return
        if self.phase != "reasoning":
            self.phase = "reasoning"
        self.reasoning += text
        self.pending_reasoning += text
        self.schedule()

    def content_chunk(self, text: str):
        if not text:
            return
        if self.phase == "reasoning":
            self.phase = "content"
        elif self.phase == "idle":
            self.phase = "content"
        self.content += text
        self.pending_content += text
        self.schedule()

    def flush(self):
        log = self.view.chat_log
        if self.pending_reasoning:
            log.append_stream("reasoning", self.pending_reasoning)
            self.pending_reasoning = ""
        if self.pending_content:
            log.append_stream("text", self.pending_content)
            self.pending_content = ""

    # ── 完成 / 失败 ──────────────────────────────────────────

    def _finalize_stream(self):
        """定稿：完整事件回传网页端，与流式行去重合并。"""
        log = self.view.chat_log
        if self.content:
            log.append_event(_final_event(self.content, "answer"))
        if self.reasoning:
            log.append_event(_final_event(self.reasoning, "reasoning"))

    def finished(self, worker_sid):
        worker = self.view.requests.workers.pop(worker_sid, None)
        if worker:
            self.vm.save_stream_result(worker_sid, worker.accumulated_content, worker.accumulated_reasoning)
        if not self.vm.session or self.vm.session.id != worker_sid:
            return
        self.vm.set_sending(False)
        self.view._refresh_context_ring()
        self.drain_buffer()
        self.timer.stop()
        self.flush()
        # 只有思考没有正文时，把思考内容作为回复展示
        if not self.content.strip() and self.reasoning.strip():
            self.content, self.reasoning = self.reasoning, ""
        self._finalize_stream()
        self.detach()

    def sync_finished(self, worker_sid: str, content: str, reasoning: str):
        """非流式模式：一次性收到完整回复。"""
        self.view.requests.workers.pop(worker_sid, None)

        content, reasoning = ChatViewModel.parse_think_tags(content, reasoning)
        if not content.strip() and reasoning.strip():
            content = reasoning
            reasoning = ""

        self.vm.save_sync_result(worker_sid, content, reasoning)

        if self.vm.session and self.vm.session.id == worker_sid:
            self.vm.set_sending(False)
            if reasoning.strip():
                self.view.chat_log.append_event(_final_event(reasoning, "reasoning"))
            self.view.chat_log.append_event(_final_event(content, "answer"))
            self.view._refresh_context_ring()

    def failed(self, worker_sid, error_msg):
        self.view.requests.workers.pop(worker_sid, None)
        if not self.vm.session or self.vm.session.id != worker_sid:
            return
        self.vm.set_sending(False)
        self.view._refresh_context_ring()
        # 部分回复保留，追加错误事件；无正文时仅提示，不产生气泡。
        partial = self.content.strip() or self.reasoning.strip() or self.buffer.strip()
        if partial:
            self.timer.stop()
            self.drain_buffer()
            self.flush()
            if not self.content.strip() and self.reasoning.strip():
                self.content, self.reasoning = self.reasoning, ""
            self._finalize_stream()
            self.view.chat_log.append_event(
                ProjectEvent(kind="error", content=f"生成中断：{error_msg}", created_at="", meta={})
            )
            self.detach()
        else:
            self.detach()
            self.view.options.tip(f"AI 回复失败:\n{error_msg}")
