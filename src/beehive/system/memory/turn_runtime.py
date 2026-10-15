"""单轮记忆生命周期和收尾；会话目录与目标由业务显式提供。"""
from __future__ import annotations
import logging
import uuid

log = logging.getLogger("wokbee")

def run_memory_turn(self, operation, finalize):
    if getattr(self, "_memory_active", False):
        return operation()
    self._memory_active = True
    self._memory_turn_id = uuid.uuid4().hex
    self._memory_reads = {}
    self._memory_notifications = {"atomic": [], "global": []}
    self._memory_model = None
    result = None
    try:
        result = operation()
        return result
    finally:
        try:
            # 审批尚未解决时不视为已完成的轮次。
            if result is not None and result.outcome != "awaiting_approval":
                finalize(result)
        except Exception as exc:
            log.exception("会话记忆收尾失败")
            self._emit("error", f"记忆整理失败，原始聊天保留：{exc}")
        finally:
            self._memory_active = False
            self._emit("info", "— 本轮运行/对话结束 —", {"session_end": True})

def finalize_memory(runner, session_root, goal, result):
    # 本轮记录已随事件流实时写入 memory/events.jsonl（含用户输入、最终回复与
    # 工具轨迹），不再另存 session_memory.md，也不重复保存另一份事件日志。
    del goal, session_root
    try:
        runner._emit(
            "info",
            "【会话记忆】本轮记录已写入 memory/events.jsonl",
            {"memory_kind": "session", "memory_turn_id": getattr(runner, "_memory_turn_id", "")},
        )
        state = getattr(runner, "_memory_notifications", {})
        atomic = state.get("atomic") or []
        runner._emit("info", "【原子记忆】\n" + ("\n".join(atomic) if atomic else "本轮未新增或修订原子记忆。"),
                     {"memory_kind": "atomic"})
        global_updates = state.get("global") or []
        runner._emit("info", "【全局记忆】\n" + ("\n".join(global_updates) if global_updates else "本轮未提交全局记忆更新建议。"),
                     {"memory_kind": "global"})
    except Exception:
        log.exception("记忆收尾通知失败")
