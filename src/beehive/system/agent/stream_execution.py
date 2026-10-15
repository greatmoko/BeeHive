"""流式推进、增量节流、步骤限制与取消收尾。"""
from __future__ import annotations
import logging
import threading
import time
from langgraph.errors import GraphInterrupt, GraphRecursionError
from beehive.system.agent.step_budget import StepLimitExceeded
from beehive.system.agent.message_content import _is_ai_message, _stream_delta_parts
from beehive.system.agent.message_content import _collect_messages_from_update

logger = logging.getLogger("wokbee")

def stream_until_pause(runtime, agent, input_payload, config, seen, *, step_budget, consume_updates, emit_message, check_prefix, emit_stream_delta, emit):
    if runtime._cancel.is_set():
        return
    if step_budget is not None:
        step_budget.consume("agent_turn")

    from beehive.common.process.subprocess_util import kill_all_cancellable_runs

    stream_error: list[BaseException] = []

    def _run_stream() -> None:
        pending: dict[str, list[str]] = {"reasoning": [], "text": []}
        last_flush = 0.0
        FLUSH_INTERVAL = 0.08

        def _flush(force: bool = False) -> None:
            nonlocal last_flush
            now = time.monotonic()
            if not force and now - last_flush < FLUSH_INTERVAL:
                return
            for _target in ("reasoning", "text"):
                parts = pending[_target]
                if not parts:
                    continue
                delta = "".join(parts)
                parts.clear()
                if delta:
                    emit_stream_delta(_target, delta)
            last_flush = now

        try:
            for chunk in agent.stream(
                input_payload,
                config=config,
                stream_mode=["messages", "updates"],
            ):
                if runtime._cancel.is_set():
                    break
                mode, payload = chunk
                if mode == "messages":
                    msg_chunk, _meta = payload
                    if _is_ai_message(msg_chunk):
                        r_delta, t_delta = _stream_delta_parts(msg_chunk)
                        if r_delta:
                            pending["reasoning"].append(r_delta)
                        if t_delta:
                            pending["text"].append(t_delta)
                        _flush()
                else:
                    _flush(force=True)
                    messages = _collect_messages_from_update(payload)
                    consume_updates(messages)
                    for msg in messages:
                        emit_message(msg, seen)
            _flush(force=True)
        except GraphInterrupt:
            pass
        except StepLimitExceeded as e:
            stream_error.append(e)
        except GraphRecursionError:
            if step_budget is not None:
                stream_error.append(
                    StepLimitExceeded(
                        step_budget.limit,
                        step_budget.used,
                        "graph_recursion",
                    )
                )
            else:
                stream_error.append(
                    RuntimeError("Deep Agents graph recursion limit reached")
                )
        except Exception as e:  # noqa: BLE001
            stream_error.append(e)

    t = threading.Thread(target=_run_stream, name="wokbee-agent-stream", daemon=True)
    t.start()
    while t.is_alive():
        t.join(0.25)
        if not runtime._cancel.is_set():
            continue
        kill_all_cancellable_runs(cancel_event=runtime._cancel)
        t.join(8)
        if t.is_alive():
            logger.warning("暂停后 stream 未退出，放弃等待并结束本轮")
            emit("info", "已暂停：正在退出执行管线（已终止本机命令）。")
        break
    if stream_error and not runtime._cancel.is_set():
        raise stream_error[0]
    check_prefix(agent, config)
