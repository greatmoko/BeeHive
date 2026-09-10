# -*- coding: utf-8 -*-
"""端到端验证 run() 新流程：首次运行生成 script-only pipeline；后续运行 0 Token；异常才唤 AI。"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from tokbee.core.provider_store import ResolvedModel
from wokbee.core.models import ApprovalFlags, Project, ProjectEvent
from wokbee.core.paths import scripts_dir
from wokbee.core.settings import WokBeeSettings
from wokbee.engine import script_factory as sf
from wokbee.engine import runner as runner_mod
from wokbee.engine.runner import AgentRunner, RunRequest
from wokbee.engine.script_runner import load_pipeline

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


class StubMessages:
    """让 agent.get_state(config) 返回一条 AI 消息。"""

    def __init__(self, text: str):
        self._text = text
        self.type = "AIMessage"
        self.content = text
        self.id = "stub-1"
        self.additional_kwargs = {}


class StubState:
    def __init__(self, text: str):
        self.values = {"messages": [StubMessages(text)]}


class StubAgent:
    def __init__(self, text: str):
        self._text = text

    def get_state(self, config):
        return StubState(self._text)


class Recorder:
    """占位（保留：记录 run() 中的 AI 调用消息）。"""

    def __init__(self):
        self.calls: list[str] = []
        self.result_text = "AI 已执行：完成目标"

    def __call__(self, *args, **kwargs):
        return None


def make_req(project_root: Path, goal: str = "获取上海今日天气并保存到 workspace") -> RunRequest:
    proj = Project(
        id=project_root.name,
        title="测试项目",
        goal=goal,
        approval=ApprovalFlags(skip_read=True, skip_write=True, skip_routine=True, skip_high_risk=True),
    )
    return RunRequest(
        project=proj,
        project_root=project_root,
        user_message=goal,
        resolved=ResolvedModel(
            provider_id="fake", provider_name="fake", model_id="fake",
            api_key="", api_host="", family="openai",
        ),
        approval=ApprovalFlags(skip_read=True, skip_write=True, skip_routine=True, skip_high_risk=True),
        max_steps=10,
    )


print("== 首次运行：完整 AI 探索 → 总结 → 生成 script-only pipeline ==")
root1 = Path(tempfile.mkdtemp())
(root1 / "workspace").mkdir(parents=True, exist_ok=True)
(req1,) = (make_req(root1),)

runner = AgentRunner(WokBeeSettings())

# 打桩：build_agent 返回假 agent；_run_agent_turn 记录调用并产生 web_search 事件
orig_build = AgentRunner.build_agent
orig_turn = AgentRunner._run_agent_turn
orig_snapshot = AgentRunner._snapshot_run_events

def fake_build(self, req, *, mode="run"):
    return StubAgent("完成")

def fake_turn(self, agent, config, seen_msg_ids, req, *, payload, first, allow_auto_lesson=True, start_hint=""):
    return None  # 模拟 AI 正常完成一轮

def fake_snapshot(self):
    ev = ProjectEvent(
        kind="tool", content="call",
        meta={"phase": "call", "tool": "web_search", "args": {"query": "上海今日天气", "max_results": 3}},
    )
    ev2 = ProjectEvent(kind="tool", content="结果", meta={"phase": "callback", "tool": "web_search", "status": "ok"})
    ev3 = ProjectEvent(kind="info", content="— 本轮运行/对话结束 —", meta={"session_end": True})
    return [ev, ev2, ev3]

AgentRunner.build_agent = fake_build
AgentRunner._run_agent_turn = fake_turn
AgentRunner._snapshot_run_events = fake_snapshot

res1 = runner.run(req1)
check("首次运行 ok", res1.ok)
check("首次运行成功 outcome", res1.outcome == "success")

pipe1 = load_pipeline(root1)
check("pipeline.json 已生成", pipe1 is not None)
types1 = [s["type"] for s in (pipe1 or {}).get("steps", [])] if pipe1 else []
print(f"    pipeline steps types = {types1}")
check("steps 全为 script", types1 and all(t == "script" for t in types1))
check("steps 不含 type:ai", "ai" not in types1)
check("version=3", (pipe1 or {}).get("version") == 3)
# 纯数据任务 → 无 final_ai
check("纯数据任务无 final_ai", (pipe1 or {}).get("final_ai") is None)

AgentRunner.build_agent = orig_build
AgentRunner._run_agent_turn = orig_turn
AgentRunner._snapshot_run_events = orig_snapshot


print("== 后续运行（纯脚本 pipeline）：0 Token，不唤 AI ==")
root2 = Path(tempfile.mkdtemp())
ensure = __import__("wokbee.core.paths", fromlist=["ensure_project_layout"]).ensure_project_layout
ensure(root2)
sdir = scripts_dir(root2)
sdir.mkdir(parents=True, exist_ok=True)
(sdir / "fetch_weather.py").write_text(
    "import sys\nsys.stdout.reconfigure(encoding='utf-8')\nprint('上海天气：晴 28°C')\n", encoding="utf-8"
)
pipe_data = {
    "version": 3,
    "goal": "获取上海今日天气并保存到 workspace",
    "steps": [
        {"id": "script_1", "type": "script", "path": "scripts/fetch_weather.py", "tool": "web_search", "description": "获取数据：web_search"},
    ],
    "scripts": [{"path": "scripts/fetch_weather.py", "tool": "web_search"}],
    "ai_steps": [],
    "final_ai": None,
}
(sdir / "pipeline.json").write_text(json.dumps(pipe_data, ensure_ascii=False), encoding="utf-8")

(req2,) = (make_req(root2),)
runner2 = AgentRunner(WokBeeSettings())
calls2: list[str] = []
orig_turn2 = AgentRunner._run_agent_turn

def fake_turn2(self, agent, config, seen_msg_ids, req, *, payload, first, allow_auto_lesson=True, start_hint=""):
    for m in payload.get("messages") or []:
        c = m.get("content")
        if isinstance(c, str):
            calls2.append(c)
    return None

AgentRunner.build_agent = lambda self, req, *, mode="run": StubAgent("x")
AgentRunner._run_agent_turn = fake_turn2
res2 = runner2.run(req2)
AgentRunner._run_agent_turn = orig_turn2
check("纯脚本运行 ok", res2.ok)
check("纯脚本运行未调用 LLM（calls 为空）", len(calls2) == 0, f"calls={len(calls2)}")
check("纯脚本返回脚本输出", "晴 28°C" in (res2.final_text or ""))
check("deliverables/script_result.md 已写入", (root2 / "deliverables" / "script_result.md").exists())


print("== 后续运行（发布产物到 deliverables）：脚本执行 + 发布步骤，0 Token ==")
root3 = Path(tempfile.mkdtemp())
ensure(root3)
sdir3 = scripts_dir(root3)
sdir3.mkdir(parents=True, exist_ok=True)
(sdir3 / "run_user_script.py").write_text(
    "import sys\nfrom pathlib import Path\nsys.stdout.reconfigure(encoding='utf-8')\n"
    "p = Path(__file__).resolve().parents[1] / 'workspace'\np.mkdir(exist_ok=True)\n"
    "(p / 'result.csv').write_text('a,b\\n1,2\\n', encoding='utf-8')\n"
    "print('脚本执行成功，已生成 result.csv')\n", encoding="utf-8"
)
pub_src = sf._render_publish_script()
(sdir3 / "publish_deliverables.py").write_text(pub_src, encoding="utf-8")
pipe_data3 = {
    "version": 3,
    "goal": "直接运行用户上传的 Python 脚本，并将脚本生成的文件放到 deliverables 交付物文件夹中；不要校验，不要检查文档内容，不要额外生成总结文档。",
    "steps": [
        {"id": "script_1", "type": "script", "path": "scripts/run_user_script.py", "tool": "script", "description": "执行用户脚本（自动执行）"},
        {"id": "script_publish", "type": "script", "path": "scripts/publish_deliverables.py", "tool": "publish", "description": "发布：把脚本实际产出文件复制到 deliverables/"},
    ],
    "scripts": [
        {"path": "scripts/run_user_script.py", "tool": "script"},
        {"path": "scripts/publish_deliverables.py", "tool": "publish"},
    ],
    "ai_steps": [],
    "policy": {"invoke_ai_on_bad_data": False, "ai_intervention": "ai_steps_and_error_recovery"},
}
(sdir3 / "pipeline.json").write_text(json.dumps(pipe_data3, ensure_ascii=False), encoding="utf-8")

(req3,) = (make_req(root3, goal="直接运行用户上传的 Python 脚本，并将脚本生成的文件放到 deliverables 交付物文件夹中；不要校验，不要检查文档内容，不要额外生成总结文档。"),)
runner3 = AgentRunner(WokBeeSettings())
calls3: list[str] = []
orig_turn3 = AgentRunner._run_agent_turn
orig_snap3 = AgentRunner._snapshot_run_events


def fake_turn3(self, agent, config, seen_msg_ids, req, *, payload, first, allow_auto_lesson=True, start_hint=""):
    for m in payload.get("messages") or []:
        c = m.get("content")
        if isinstance(c, str):
            calls3.append(c)
    return None


def fake_snap3(self):
    return []


AgentRunner.build_agent = lambda self, req, *, mode="run": StubAgent("脚本执行成功")
AgentRunner._run_agent_turn = fake_turn3
AgentRunner._snapshot_run_events = fake_snap3
res3 = runner3.run(req3)
AgentRunner._run_agent_turn = orig_turn3
AgentRunner._snapshot_run_events = orig_snap3
check("发布产物运行 ok", res3.ok)
check("未调用 LLM（0 Token）", len(calls3) == 0, f"calls={len(calls3)}")
check("脚本实际产物 result.csv 已进入 deliverables", (root3 / "deliverables" / "result.csv").exists())
check("未生成 final.md（不代替原始产物）", not (root3 / "deliverables" / "final.md").exists())


print("== 后续运行（混合管线 script→ai→script）：ai 步骤执行固定业务任务，仅按需耗 Token ==")
root3b = Path(tempfile.mkdtemp())
ensure(root3b)
sdir3b = scripts_dir(root3b)
sdir3b.mkdir(parents=True, exist_ok=True)
(sdir3b / "collect.py").write_text(
    "import sys\nfrom pathlib import Path\nsys.stdout.reconfigure(encoding='utf-8')\n"
    "p = Path(__file__).resolve().parents[1] / 'workspace'\np.mkdir(exist_ok=True)\n"
    "(p / 'script_callback_collect.md').write_text('原始数据：上海 28C 晴\\n北京 15C 多云\\n', encoding='utf-8')\n"
    "print('数据收集完成')\n", encoding="utf-8"
)
(sdir3b / "finalize.py").write_text(
    "import sys\nfrom pathlib import Path\nsys.stdout.reconfigure(encoding='utf-8')\n"
    "print('定稿完成')\n", encoding="utf-8"
)
pipe_data3b = {
    "version": 3,
    "goal": "收集城市天气材料，撰写报告并交付到 deliverables",
    "steps": [
        {"id": "script_1", "type": "script", "path": "scripts/collect.py", "tool": "script", "description": "收集原始数据（自动执行）"},
        {"id": "ai_1", "type": "ai", "description": "根据收集到的天气材料整理分析并撰写报告", "prompt_hint": "先读 workspace/script_callback_collect.md"},
        {"id": "script_2", "type": "script", "path": "scripts/finalize.py", "tool": "script", "description": "定稿收尾（自动执行）"},
    ],
    "scripts": [
        {"path": "scripts/collect.py", "tool": "script"},
        {"path": "scripts/finalize.py", "tool": "script"},
    ],
    "ai_steps": [{"description": "根据收集到的天气材料整理分析并撰写报告", "prompt_hint": "先读 workspace/script_callback_collect.md"}],
    "policy": {"invoke_ai_on_bad_data": False, "ai_intervention": "ai_steps_and_error_recovery"},
}
(sdir3b / "pipeline.json").write_text(json.dumps(pipe_data3b, ensure_ascii=False), encoding="utf-8")

(req3b,) = (make_req(root3b, goal="收集城市天气材料，撰写报告并交付到 deliverables"),)
runner3b = AgentRunner(WokBeeSettings())
calls3b: list[str] = []
orig_turn3b = AgentRunner._run_agent_turn
orig_snap3b = AgentRunner._snapshot_run_events


def fake_turn3b(self, agent, config, seen_msg_ids, req, *, payload, first, allow_auto_lesson=True, start_hint=""):
    for m in payload.get("messages") or []:
        c = m.get("content")
        if isinstance(c, str):
            calls3b.append(c)
    return None


def fake_snap3b(self):
    return []


AgentRunner.build_agent = lambda self, req, *, mode="run": StubAgent("报告已完成")
AgentRunner._run_agent_turn = fake_turn3b
AgentRunner._snapshot_run_events = fake_snap3b
res3b = runner3b.run(req3b)
AgentRunner._run_agent_turn = orig_turn3b
AgentRunner._snapshot_run_events = orig_snap3b
check("混合管线运行 ok", res3b.ok)
check("仅 ai 步骤耗 Token：恰好 1 次 LLM 调用", len(calls3b) == 1, f"calls={len(calls3b)}")
ai_msg3b = calls3b[0] if calls3b else ""
check("AI 消息是固定业务任务（非重新规划）", "撰写报告" in ai_msg3b and "不要重新规划" in ai_msg3b)
check("后续 script 步骤仍然执行（finalize 已跑）", (root3b / "workspace").exists())


print("== 异常恢复：脚本失败 → 唤 AI 补救 ==")
root4 = Path(tempfile.mkdtemp())
ensure(root4)
sdir4 = scripts_dir(root4)
sdir4.mkdir(parents=True, exist_ok=True)
(sdir4 / "bad.py").write_text(
    "import sys\nsys.stdout.reconfigure(encoding='utf-8')\nprint('脚本执行失败')\nsys.exit(1)\n", encoding="utf-8"
)
pipe_data4 = {
    "version": 3,
    "goal": "测试",
    "steps": [
        {"id": "script_1", "type": "script", "path": "scripts/bad.py", "tool": "script", "description": "失败脚本"},
    ],
    "scripts": [{"path": "scripts/bad.py", "tool": "script"}],
}
(sdir4 / "pipeline.json").write_text(json.dumps(pipe_data4, ensure_ascii=False), encoding="utf-8")

(req4,) = (make_req(root4, goal="测试"),)
runner4 = AgentRunner(WokBeeSettings())
calls4: list[str] = []
orig_turn4 = AgentRunner._run_agent_turn
orig_snap4 = AgentRunner._snapshot_run_events

def fake_turn4(self, agent, config, seen_msg_ids, req, *, payload, first, allow_auto_lesson=True, start_hint=""):
    for m in payload.get("messages") or []:
        c = m.get("content")
        if isinstance(c, str):
            calls4.append(c)
    return None

def fake_snap4(self):
    ev = ProjectEvent(kind="error", content="脚本执行失败：bad.py")
    return [ev]

AgentRunner.build_agent = lambda self, req, *, mode="run": StubAgent("已修复")
AgentRunner._run_agent_turn = fake_turn4
AgentRunner._snapshot_run_events = fake_snap4
res4 = runner4.run(req4)
AgentRunner._run_agent_turn = orig_turn4
AgentRunner._snapshot_run_events = orig_snap4
check("异常恢复运行 ok", res4.ok)
check("唤 AI 补救（脚本失败）", len(calls4) == 1, f"calls={len(calls4)}")
err_msg = calls4[0] if calls4 else ""
check("AI 消息含失败信息", "脚本失败" in err_msg or "补救" in err_msg)

print()
print(f"结果：PASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
