"""手机澄清问题与答复的业务协调。"""
from __future__ import annotations
import threading
from beehive.system.gateway.base import ChannelMessage
class PhoneQuestions:
    """按频道会话持有待答问题与同步事件。"""
    def __init__(self, reply, notifier):
        self._reply = reply
        self.notifier = notifier
        self._pending_asks = {}
        self._pending_asks_guard = threading.Lock()

    @staticmethod
    def _ask_key(msg: ChannelMessage) -> str:
        return f"{msg.channel}::{msg.conversation_id or msg.sender_id}"

    def _wait_phone_answer(self, context: ChannelMessage, timeout: float) -> str | None:
        """注册一个待答问题并阻塞等待手机回信；超时/无答复返回 None。"""
        key = self._ask_key(context)
        holder = {"event": threading.Event(), "reply": None, "key": key}
        with self._pending_asks_guard:
            self._pending_asks[key] = holder
        try:
            waited = holder["event"].wait(timeout)
        finally:
            with self._pending_asks_guard:
                self._pending_asks.pop(key, None)
        if not waited:
            self._reply(context, "等待你的答复超时，本次操作已中止。")
            return None
        return holder["reply"]

    def _resolve_pending(self, msg: ChannelMessage) -> bool:
        """若该会话正等待澄清答复，则用 msg.text 作为答案并解除阻塞。返回是否已消费。"""
        key = self._ask_key(msg)
        with self._pending_asks_guard:
            holder = self._pending_asks.get(key)
            if holder is None:
                return False
            holder["reply"] = (msg.text or "").strip()
        self.notifier.log_line.emit(f"[澄清] 收到 {msg.sender_id} 的答复：{(msg.text or '')[:60]}")
        holder["event"].set()
        return True

    def _ask_user_sink(self, payload: dict, context: ChannelMessage) -> dict:
        """把澄清问题逐题发到用户手机上，收集答复后返回 answers 字典。"""
        questions = payload.get("questions") or []
        if not questions:
            return {"cancelled": True}
        collected: list[dict] = []
        for i, q in enumerate(questions, 1):
            if not isinstance(q, dict):
                continue
            qid = str(q.get("id") or f"q{i}")
            opts = q.get("options") or []
            lines = [f"WokBee 需要你确认第 {i} 项：", f"{q.get('prompt') or ''}"]
            if opts:
                lines.append("可回复：" + "；".join(f"{j}. {o}" for j, o in enumerate(opts, 1)))
            lines.append("直接回复内容或序号即可；回复「取消」可中止。")
            self._reply(context, "\n".join(lines))

            reply = self._wait_phone_answer(context, timeout=600.0)
            if reply is None:
                return {"cancelled": True}
            if reply in ("取消", "cancel", "中止"):
                return {"cancelled": True}

            selected: list[str] = []
            if reply.isdigit():
                n = int(reply)
                if 1 <= n <= len(opts):
                    selected = [str(opts[n - 1])]
            if not selected:
                selected = [reply]
            collected.append({"id": qid, "selected": selected, "custom": ""})
        return {"answers": collected}
