"""业务运行请求到通用记忆生命周期的适配。"""
from functools import wraps
from beehive.system.memory.turn_runtime import run_memory_turn, finalize_memory as finalize_session_memory

def memory_turn(fn):
    @wraps(fn)
    def wrapped(self, req, *args, **kwargs):
        return run_memory_turn(self, lambda: fn(self, req, *args, **kwargs),
                               lambda result: finalize_memory(self, req, result))
    return wrapped

def finalize_memory(runner, req, result):
    finalize_session_memory(runner, req.project_root,
                            req.user_message or req.project.goal or "", result)
    # 本轮事件全部落盘，关闭轮次上下文（等待审批时不收尾，恢复后仍是同一轮）。
    try:
        from beehive.features.wokbee.projects.round_events import end_round
        end_round(req.project.id)
    except Exception:
        pass
