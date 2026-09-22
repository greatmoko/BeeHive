import copy
import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wokbee.core.memory import MemoryStore, SessionMemory


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = MemoryStore(self.root / "memory.sqlite3")
        self.session = SessionMemory(self.root)

    def test_session_append_idempotence_filters_and_budget(self):
        for i in range(8):
            self.session.append(f"turn-{i}", goal=f"需求{i}", result="完成", keywords="测试")
        self.session.append("turn-0", goal="不应覆盖", result="不应覆盖")
        self.assertEqual(len(self.session.records()), 8)
        self.assertNotIn("不应覆盖", self.session.path.read_text(encoding="utf-8"))
        self.assertEqual(len(self.session.read(recent=2).splitlines()), 2)
        self.assertEqual(len(self.session.read(rounds=[str(i) for i in range(1, 9)]).splitlines()), 8)
        self.assertIn("需求3", self.session.read(rounds=["turn-3"]))
        self.assertIn("需求4", self.session.read(keyword="需求4"))
        record = self.session.records()[0]
        self.assertIn("需求0", self.session.read(start_line=record["line"], end_line=record["line"]))
        big = self.session.append("big", goal="中" * 3000, result="文" * 3000, unresolved="长" * 3000, keywords="词" * 3000)
        self.assertLessEqual(len(big["text"]), 1000)

    def test_legacy_session_memory_is_migrated_without_deleting_source(self):
        legacy = self.root / ".wokbee" / "session_memory.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("\n<!-- turn:legacy -->\n旧记录\n", encoding="utf-8")
        session = SessionMemory(self.root)
        self.assertEqual(session.path, self.root / "memory" / "session_memory.md")
        self.assertIn("旧记录", session.path.read_text(encoding="utf-8"))
        self.assertTrue(legacy.exists())

    def test_atomic_versions_counts_dedup_and_conditions(self):
        first = self.store.write(["工作电脑", "系统"], "事实", "工作电脑 Windows")
        second = self.store.write(["个人电脑", "系统"], "事实", "个人电脑 macOS")
        self.assertEqual(len(self.store.search(["系统"])), 2)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT sum(retrieval_count) FROM atomic_memory").fetchone()[0], 0)
        self.assertEqual(self.store.read([first, first])[0]["retrieval_count"], 1)
        new = self.store.write(["工作电脑", "系统"], "事实", "工作电脑 Ubuntu", previous_id=first)
        self.assertEqual({r["id"] for r in self.store.search(["系统"])}, {second, new})
        self.assertEqual(self.store.read([new])[0]["version"], 2)
        self.assertEqual(self.store.read([first])[0]["body"], "工作电脑 Windows")
        self.assertEqual(new, self.store.write(["系统"], "事实", "工作电脑 Ubuntu"))
        with self.assertRaises(ValueError):
            self.store.write(["系统"], "事实", "过期修订", previous_id=first)

    def test_atomic_search_matches_any_keyword_fragment(self):
        weather = self.store.write(["深圳天气", "weather.com.cn"], "事实", "天气数据源")
        notes = self.store.write(["小红书笔记"], "偏好", "文章风格")
        found = {row["id"] for row in self.store.search(["湘潭天气", "小红书文章"])}
        self.assertEqual(found, {weather, notes})

    def test_global_proposals_default_no_conflicts_restore_limit(self):
        original = self.store.global_memory()
        ident = self.store.propose("用户画像", "默认中文", "本轮明确要求")
        self.assertEqual(self.store.global_memory(), original)
        self.store.decide(ident)
        self.assertEqual(self.store.global_memory(), original)
        self.store.save_global({**self.store.global_memory()["content"], "用户画像": "默认中文"})
        first = self.store.propose_rule("用户画像", "replace", "默认英文", "本轮明确要求", target="默认中文")
        stale = self.store.propose_rule("用户画像", "replace", "默认日文", "另一轮", target="默认中文")
        self.store.decide(first, accept=True)
        with self.assertRaises(ValueError):
            self.store.decide(stale, accept=True)
        self.store.restore(1)
        self.assertEqual(self.store.global_memory()["content"], original["content"])
        self.assertEqual(self.store.global_memory()["version"], 3)
        with self.assertRaises(ValueError):
            self.store.propose_rule("全局规则", "add", "中" * 50001, "太长")

    def test_global_proposal_replaces_exact_rule_not_line_number(self):
        content = self.store.global_memory()["content"]
        self.store.save_global({**content, "用户画像": "规则甲\n规则乙"})
        ident = self.store.propose_rule("用户画像", "replace", "规则乙新版", "纠正", target="规则乙")
        self.store.save_global({**self.store.global_memory()["content"], "用户画像": "新增规则\n规则甲\n规则乙"})
        self.store.decide(ident, accept=True)
        self.assertEqual(self.store.global_memory()["content"]["用户画像"], "新增规则\n规则甲\n规则乙新版")

    def test_global_proposal_strips_repeated_module_heading(self):
        ident = self.store.propose_rule("用户画像", "add", "【用户画像】 职业：产品经理", "用户说明")
        proposal = next(item for item in self.store.proposals() if item["id"] == ident)
        self.assertEqual(proposal["new"], "职业：产品经理")

    def test_concurrent_append_and_version_branch_rejection(self):
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda i: self.session.append(f"turn-{i}", goal="并发", result="完成"), range(20)))
        self.assertEqual(len(self.session.records()), 20)
        old = self.store.write(["规则"], "规则", "原规则")
        def revise(i):
            try:
                return self.store.write(["规则"], "规则", f"修订{i}", previous_id=old)
            except ValueError:
                return None
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(revise, range(4)))
        self.assertEqual(sum(bool(r) for r in results), 1)

    def test_threshold_validation(self):
        self.assertEqual(self.store.thresholds(), (.8, .3))
        self.store.set_thresholds(.75, .25)
        self.assertEqual(MemoryStore(self.store.path).thresholds(), (.75, .25))
        for pair in ((.3, .8), (.8, .8), (float("nan"), .2), (1.1, .2), (.8, 0)):
            with self.assertRaises(ValueError):
                self.store.set_thresholds(*pair)

    def test_memory_rules_require_agent_driven_queries(self):
        from wokbee.core.memory import MEMORY_RULES

        self.assertIn("三级记忆机制", MEMORY_RULES)
        self.assertIn("get_project_info", MEMORY_RULES)
        self.assertIn("非首次运行时", MEMORY_RULES)
        self.assertIn("read_session_memory", MEMORY_RULES)
        self.assertIn("search_atomic_memory", MEMORY_RULES)
        self.assertIn("read_atomic_memory", MEMORY_RULES)
        self.assertIn("最多再搜索一次", MEMORY_RULES)
        self.assertIn("用户画像", MEMORY_RULES)
        self.assertIn("长期身份、偏好、习惯或约束", MEMORY_RULES)

    def middleware(self, window=6000):
        from deepagents.backends import StateBackend
        from langchain_core.language_models.fake_chat_models import FakeListChatModel
        from wokbee.engine.memory_context import SessionMemoryMiddleware
        return SessionMemoryMiddleware(FakeListChatModel(responses=["摘要"]), backend=StateBackend(),
                                       session=self.session, store=self.store, context_window=window, emit=Mock())

    def test_context_replaces_oldest_whole_turn_and_keeps_originals(self):
        from langchain.agents.middleware.types import ModelRequest, ModelResponse
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
        middleware = self.middleware()
        self.session.append("first", goal="早期", result="已完成")
        self.session.append("second", goal="后期", result="已完成")
        messages = [HumanMessage(content="中" * 3400, additional_kwargs={"memory_turn_id": "first", "memory_global": "固定全局快照"}),
                    AIMessage(content="", tool_calls=[{"id": "call", "name": "read", "args": {}}]),
                    ToolMessage(content="结果" * 1000, tool_call_id="call"), AIMessage(content="完成"),
                    HumanMessage(content="后续", additional_kwargs={"memory_turn_id": "second"}), AIMessage(content="完成"),
                    HumanMessage(content="现在", additional_kwargs={"memory_turn_id": "current"})]
        before = copy.deepcopy(messages)
        request = ModelRequest(model=middleware.summary_model, messages=messages, state={"messages": messages})
        handler = Mock(return_value=ModelResponse(result=[AIMessage(content="答复")]))
        middleware.wrap_model_call(request, handler)
        delivered = handler.call_args.args[0].messages
        self.assertTrue(any("会话记忆 first" in str(m.content) for m in delivered))
        self.assertFalse(any(getattr(m, "type", "") == "tool" for m in delivered))
        self.assertIn("后续", [m.content for m in delivered])
        self.assertIn("现在", [m.content for m in delivered])
        self.assertIn("固定全局快照", delivered[0].content)
        self.assertEqual(messages, before)

    def test_fallback_only_after_all_complete_turns_replaced(self):
        from deepagents.middleware.summarization import SummarizationMiddleware
        from langchain.agents.middleware.types import ModelRequest, ModelResponse
        from langchain_core.messages import HumanMessage, AIMessage
        middleware = self.middleware(window=2000)
        self.session.append("first", goal="旧轮", result="完成")
        messages = [HumanMessage(content="中" * 2000, additional_kwargs={"memory_turn_id": "first"}),
                    AIMessage(content="完成"), HumanMessage(content="大" * 1500, additional_kwargs={"memory_turn_id": "current"})]
        original = copy.deepcopy(messages)
        request = ModelRequest(model=middleware.summary_model, messages=messages, state={"messages": messages})
        response = ModelResponse(result=[AIMessage(content="结果")])
        with patch.object(SummarizationMiddleware, "wrap_model_call", return_value=SimpleNamespace(model_response=response, command="must not commit")) as fallback:
            self.assertIs(middleware.wrap_model_call(request, Mock()), response)
            projection = fallback.call_args.args[0].messages
            self.assertTrue(any("会话记忆 first" in str(m.content) for m in projection))
            self.assertEqual(projection[-1], messages[-1])
        self.assertEqual(messages, original)

    def test_finalizer_appends_without_a_second_model_call(self):
        from wokbee.engine.memory_runtime import finalize_memory
        model = Mock()
        runner = SimpleNamespace(_memory_model=model, _memory_store=self.store, _memory_turn_id="one",
                                 _memory_reads={}, _cancel=threading.Event(), _snapshot_run_events=lambda: [], _emit=Mock())
        req = SimpleNamespace(project_root=self.root, user_message="测试", project=SimpleNamespace(goal="测试"),
                              resolved=SimpleNamespace(context_window=32000))
        result = SimpleNamespace(final_text="完成", error="", outcome="success", ok=True)
        finalize_memory(runner, req, result)
        self.assertEqual(len(self.session.records()), 1)
        model.bind_tools.assert_not_called()

    def test_real_graph_preserves_history_and_global_snapshot(self):
        from deepagents import create_deep_agent
        from deepagents.backends import StateBackend
        from langchain_core.language_models.fake_chat_models import FakeListChatModel
        from langgraph.checkpoint.memory import InMemorySaver
        from wokbee.engine.memory_context import SessionMemoryMiddleware

        class Model(FakeListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self

        model = Model(responses=["第一轮完成", "第二轮完成"])
        backend = StateBackend()
        middleware = SessionMemoryMiddleware(model, backend=backend, session=self.session, store=self.store,
                                             context_window=12000, emit=Mock())
        agent = create_deep_agent(model=model, backend=backend, middleware=[middleware], checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "memory-test"}}
        agent.invoke({"messages": [{"role": "user", "content": "中" * 10000,
                                    "additional_kwargs": {"memory_turn_id": "first", "memory_global": "稳定信息"}}]}, config)
        self.session.append("first", goal="目标", result="第一轮完成")
        result = agent.invoke({"messages": [{"role": "user", "content": "下一轮",
                                             "additional_kwargs": {"memory_turn_id": "second", "memory_global": "不应替换旧快照"}}]}, config)
        self.assertEqual(len(result["messages"]), 4)
        self.assertEqual(result["messages"][0].content, "中" * 10000)
        self.assertEqual(middleware._global_message.content.split("\n")[-1], "稳定信息")

    def test_atomic_writer_completes_single_call_and_never_global_apply(self):
        from wokbee.engine.memory_runtime import build_memory_tools
        old = self.store.write(["语言"], "偏好", "中文")
        tools = {t.name: t for t in build_memory_tools(self.session, self.store, {})}
        arguments = {"keywords": ["语言"], "kind": "偏好", "body": "默认英文", "previous_id": old}
        response = json.loads(tools["write_atomic_memory"].invoke(arguments))
        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["saved"], 1)
        self.assertEqual(len(self.store.search(["语言"])), 1)
        self.assertEqual(response["results"][0]["id"], self.store.search(["语言"])[0]["id"])
        self.assertEqual(set(tools), {"read_session_memory", "search_atomic_memory", "read_atomic_memory", "write_atomic_memory", "propose_global_memory"})

    def test_atomic_writer_batches_records_and_ignores_duplicate_call(self):
        from wokbee.engine.memory_runtime import build_memory_tools
        tool = next(t for t in build_memory_tools(self.session, self.store, {})
                    if t.name == "write_atomic_memory")
        memories = [
            {"keywords": ["批量一"], "kind": "事实", "body": "第一条"},
            {"keywords": ["批量二"], "kind": "规则", "body": "第二条"},
        ]
        response = json.loads(tool.invoke({"memories": memories}))
        self.assertEqual((response["status"], response["saved"]), ("completed", 2))
        self.assertEqual(json.loads(tool.invoke({"memories": memories}))["status"], "already_completed")
        self.assertEqual(len(self.store.search(["批量一"])), 1)
        self.assertEqual(len(self.store.search(["批量二"])), 1)

    def test_atomic_writer_allows_retry_after_validation_error(self):
        from wokbee.engine.memory_runtime import build_memory_tools
        tool = next(t for t in build_memory_tools(self.session, self.store, {}) if t.name == "write_atomic_memory")
        failed = json.loads(tool.invoke({"memories": [{"keywords": ["天气"], "kind": "invalid", "body": "无效"}]}))
        self.assertEqual((failed["status"], failed["saved"]), ("error", 0))
        retried = json.loads(tool.invoke({"memories": [{"keywords": ["天气"], "kind": "fact", "body": "可重试"}]}))
        self.assertEqual((retried["status"], retried["saved"]), ("completed", 1))

    def test_shared_finalizer_runs_once_and_end_marker_is_last(self):
        from wokbee.engine.memory_runtime import memory_turn
        class Runner:
            _emit = Mock()
            @memory_turn
            def run(self, req):
                return self.chat(req)
            @memory_turn
            def chat(self, req):
                return SimpleNamespace(outcome="success")
        runner = Runner()
        with patch("wokbee.engine.memory_runtime.finalize_memory") as finalize:
            runner.run(SimpleNamespace())
            finalize.assert_called_once()
        self.assertTrue(runner._emit.call_args.args[2]["session_end"])

    def test_real_framework_fallback_does_not_store_projection_cutoff(self):
        from deepagents import create_deep_agent
        from deepagents.backends import FilesystemBackend
        from langchain_core.language_models.fake_chat_models import FakeListChatModel
        from langgraph.checkpoint.memory import InMemorySaver
        from wokbee.engine.memory_context import SessionMemoryMiddleware
        class Model(FakeListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self
        model = Model(responses=["压缩后的系统摘要", "最终答复", "备用答复"])
        backend = FilesystemBackend(root_dir=self.root, virtual_mode=True)
        middleware = SessionMemoryMiddleware(model, backend=backend, session=self.session, store=self.store,
                                             context_window=4000, emit=Mock())
        messages = []
        for i in range(6):
            key = f"old-{i}"
            self.session.append(key, goal="目标" * 100, result="结果" * 200, unresolved="未解决" * 50)
            messages.extend([{"role": "user", "content": "原始历史" * 800, "additional_kwargs": {"memory_turn_id": key}},
                             {"role": "assistant", "content": "原始答复"}])
        messages.append({"role": "user", "content": "当前请求", "additional_kwargs": {"memory_turn_id": "current"}})
        agent = create_deep_agent(model=model, backend=backend, middleware=[middleware], checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "fallback"}}
        result = agent.invoke({"messages": messages}, config)
        self.assertEqual(len(result["messages"]), len(messages) + 1)
        self.assertEqual(result["messages"][0].content, "原始历史" * 800)
        self.assertFalse(agent.get_state(config).values.get("_summarization_event"))
        self.assertEqual(model.i, 2)  # one framework summary call, then the real answer

    def test_archive_keeps_session_markdown(self):
        from tokbee.core.config import Config
        from wokbee.core.settings import WokBeeSettings
        from wokbee.core.project_store import ProjectStore
        settings = WokBeeSettings(Config(str(self.root / "config.json")))
        settings.workspace_root = self.root / "projects"
        store = ProjectStore(settings)
        project = store.create("记忆归档测试")
        session = SessionMemory(store.path_for(project.id))
        session.append("persist", goal="保留", result="永久会话記忆")
        before = session.path.read_bytes()
        store.archive_session(project.id, include_memory=True)
        self.assertEqual(session.path.read_bytes(), before)
        self.assertEqual(session.path.parent.name, "memory")


if __name__ == "__main__":
    unittest.main()
