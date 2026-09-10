# -*- coding: utf-8 -*-
"""验证记忆/经验注入策略：
- 首次运行（执行管线为空）：注入 项目经验 + 记忆概述 + 跨项目相关记忆(自动召回)
- 非首次运行（已有执行管线）：仍注入 项目经验 + 记忆概述；不自动查询注入跨项目相关记忆
"""
from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from tokbee.core.provider_store import ResolvedModel
from wokbee.core.models import ApprovalFlags, Project
from wokbee.core.paths import ensure_project_layout, scripts_dir
from wokbee.core.settings import WokBeeSettings
from wokbee.engine.runner import AgentRunner, RunRequest
import wokbee.engine.runner as runner_pkg

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


def make_req(project_root: Path, goal: str) -> RunRequest:
    proj = Project(
        id=project_root.name,
        title="记忆注入测试",
        goal=goal,
        approval=ApprovalFlags(skip_read=True, skip_write=True, skip_routine=True, skip_high_risk=True),
    )
    return RunRequest(
        project=proj,
        project_root=project_root,
        user_message=goal,
        resolved=ResolvedModel(
            provider_id="openai", provider_name="openai", model_id="gpt-4o-mini",
            api_key="sk-test-local", api_host="https://api.openai.com/v1", family="openai",
        ),
        approval=ApprovalFlags(skip_read=True, skip_write=True, skip_routine=True, skip_high_risk=True),
        max_steps=10,
    )


# 打桩跨项目记忆召回（避免真实模型调用）；build_agent 只构造客户端，不发起网络调用
runner_pkg.recall_memories = lambda intent, model, k=3: (
    "## 相关记忆\n- **跨项目记忆A**：天气数据采集流程\n- **跨项目记忆B**：报告格式要求"
)

# 构造一个已有经验的项目（memory/experiences/exp_xxx.md）+ 已有执行管线 → 非首次运行
proj = Path(tempfile.mkdtemp())
ensure_project_layout(proj)
exp_dir = proj / "memory" / "experiences"
exp_dir.mkdir(parents=True, exist_ok=True)
(exp_dir / "exp_20260910_132405_933.md").write_text(
    "# 经验摘要\n执行用户脚本并发布到 deliverables。\n\n"
    "## 成功路径\n1. 脚本执行:\"{cmd: execute scripts/run_user_script.py}\";[运行脚本]\n"
    "2. 脚本执行:\"{cmd: execute scripts/publish_deliverables.py}\";[发布产物]\n",
    encoding="utf-8",
)
sdir = scripts_dir(proj)
sdir.mkdir(parents=True, exist_ok=True)
(sdir / "run_user_script.py").write_text("print('hi')\n", encoding="utf-8")
(sdir / "pipeline.json").write_text(
    '{"version": 3, "steps": [{"id": "s1", "type": "script", "path": "scripts/run_user_script.py", "tool": "script", "description": "执行"}]}',
    encoding="utf-8",
)

req = make_req(proj, "运行用户脚本并把产物放到 deliverables")
runner = AgentRunner(WokBeeSettings())

print("== 非首次运行（已有执行管线）==")
runner.build_agent(req, mode="run")
ctx = runner._session_context_block or ""
check("注入【项目经验记忆】（最新经验 exp_20260910...）", "【项目经验记忆】" in ctx and "exp_20260910_132405_933.md" in ctx)
check("注入【记忆概述】", "【记忆概述】" in ctx)
check("不自动注入【相关记忆】（跨项目）", "【相关记忆】（依本次意图" not in ctx and "跨项目记忆A" not in ctx)
check("本轮回溯说明提示按需 search_memory", "search_memory" in ctx and "非首次运行" in ctx)

print("== 首次运行（无执行管线）==")
proj2 = Path(tempfile.mkdtemp())
ensure_project_layout(proj2)
exp2 = proj2 / "memory" / "experiences"
exp2.mkdir(parents=True, exist_ok=True)
(exp2 / "exp_first_001.md").write_text(
    "# 经验摘要\n获取天气并成文。\n\n## 成功路径\n1. AI:\"{提示词: 写报告}\";[成文]\n",
    encoding="utf-8",
)
req2 = make_req(proj2, "获取上海天气并写报告")
runner2 = AgentRunner(WokBeeSettings())
runner2.build_agent(req2, mode="run")
ctx2 = runner2._session_context_block or ""
check("首次运行注入【项目经验记忆】", "【项目经验记忆】" in ctx2 and "exp_first_001.md" in ctx2)
check("首次运行注入【记忆概述】", "【记忆概述】" in ctx2)
check("首次运行自动召回【相关记忆】", "【相关记忆】（依本次意图" in ctx2 and "跨项目记忆A" in ctx2)

runner_pkg.recall_memories = None

print()
print(f"结果：PASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
