# -*- coding: utf-8 -*-
"""验证 Pipeline 新逻辑：pipeline 只含 script 步骤、发布步骤复制真实产物、无 final_ai。"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from wokbee.core.paths import ensure_project_layout, scripts_dir
from wokbee.engine import script_factory as sf
from wokbee.engine import script_runner as sr

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


print("== 1. 目标识别 ==")
check("交付目标 → 补发布步骤", sf.goal_wants_deliverables("运行脚本并把产物放到 deliverables 交付物") is True)
check("交付目标（中文交付）", sf.goal_wants_deliverables("生成文件并交付到交付物文件夹") is True)
check("非交付目标 → 不加", sf.goal_wants_deliverables("下载天气数据并保存到 workspace") is False)
check("不校验目标 → no_check", sf.goal_no_check("直接运行脚本；不要校验，不要检查文档内容") is True)
check("无 no_check 目标", sf.goal_no_check("运行脚本并发布产物") is False)

print("== 2. solidify_scripts：交付目标 → [执行脚本, publish]，无 final_ai ==")
root = Path(tempfile.mkdtemp())
ensure_project_layout(root)


class _Ev:
    def __init__(self, kind, content, meta=None):
        self.kind = kind
        self.content = content
        self.meta = meta or {}


events = [
    _Ev("tool", "call", {"phase": "call", "tool": "execute", "args": {"command": "python uploads/user_script.py"}}),
]
solid = sf.solidify_scripts(
    root, lesson_id="exp_test1",
    goal="直接运行用户上传的 Python 脚本，并将脚本生成的文件放到 deliverables 交付物文件夹中；不要校验，不要检查文档内容，不要额外生成总结文档。",
    summary="运行用户脚本并发布产物",
    events=events,
)
pipe = sr.load_pipeline(root)
types = [s.get("type") for s in (pipe or {}).get("steps", [])]
print(f"    steps types = {types}")
check("steps 全部为 script", types and all(t == "script" for t in types))
check("steps 不含 ai / 无 final_ai", "ai" not in types and "final_ai" not in (pipe or {}))
check("含执行用户脚本步骤", any("user_script" in str(s.get("path") or "") for s in (pipe or {}).get("steps", [])))
check("含发布步骤", any(str(s.get("tool") or "") == "publish" for s in (pipe or {}).get("steps", [])))
check("policy.invoke_ai_on_bad_data=False（不校验）", (pipe or {}).get("policy", {}).get("invoke_ai_on_bad_data") is False)
check("policy.ai_intervention=ai_steps_and_error_recovery", (pipe or {}).get("policy", {}).get("ai_intervention") == "ai_steps_and_error_recovery")

print("== 3. 发布脚本内容：复制真实文件，而非合并 final.md ==")
pub_step = next(s for s in (pipe or {}).get("steps", []) if str(s.get("tool") or "") == "publish")
pub_src = (root / pub_step["path"]).read_text(encoding="utf-8")
check("发布脚本复制文件到 deliverables/", "deliverables" in pub_src and "copy2" in pub_src)
check("发布脚本不做 md 合并 final.md", "final.md" not in pub_src and "自动归档" not in pub_src)
check("发布脚本排除 callback 中间记录", "script_callback_" in pub_src)

print("== 4. 非交付目标：不自动加发布步骤 ==")
root2 = Path(tempfile.mkdtemp())
ensure_project_layout(root2)
sf.solidify_scripts(
    root2, lesson_id="exp_test2",
    goal="下载天气数据并保存到 workspace",
    summary="下载数据", events=events,
)
pipe2 = sr.load_pipeline(root2)
types2 = [s.get("type") for s in (pipe2 or {}).get("steps", [])]
check("非交付目标无发布步骤", all(str(s.get("tool") or "") != "publish" for s in (pipe2 or {}).get("steps", [])))
check("policy.invoke_ai_on_bad_data=True（默认）", (pipe2 or {}).get("policy", {}).get("invoke_ai_on_bad_data") is True)

print("== 5. normalize_steps 保留 ai 步骤（已确定的业务任务）==")
data_old = {
    "steps": [
        {"id": "s1", "type": "script", "path": "scripts/a.py"},
        {"id": "a1", "type": "ai", "description": "提取要点"},
        {"id": "s2", "type": "script", "path": "scripts/b.py"},
    ]
}
run_steps = sr.normalize_steps(data_old)
check("默认保留 ai 步骤", [s["type"] for s in run_steps] == ["script", "ai", "script"])
run_steps_n = sr.normalize_steps(data_old, keep_ai=False)
check("keep_ai=False 过滤 ai", [s["type"] for s in run_steps_n] == ["script", "script"])

print("== 6. peek_pipeline：ai 步骤作为固定业务任务保留 ==")
legacy_root = Path(tempfile.mkdtemp())
ensure_project_layout(legacy_root)
legacy = {
    "version": 2,
    "goal": "写报告",
    "steps": [
        {"id": "s1", "type": "script", "path": "scripts/nope_missing.py"},
        {"id": "a1", "type": "ai", "description": "根据收集材料撰写报告", "prompt_hint": "先读 callback"},
    ],
    "scripts": [{"path": "scripts/nope_missing.py"}],
    "ai_steps": [{"description": "根据收集材料撰写报告", "prompt_hint": "先读 callback"}],
}
(scripts_dir(legacy_root)).mkdir(parents=True, exist_ok=True)
(scripts_dir(legacy_root) / "pipeline.json").write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
peek = sr.peek_pipeline(legacy_root)
check("运行态保留 ai 步骤", [s["type"] for s in peek.steps] == ["script", "ai"])
check("无 final_ai_task", getattr(peek, "final_ai_task", None) is None)

print("== 7. apply_ai_pipeline_steps：script+ai 混合、清除 final_ai ==")
steps_in = [
    {"type": "script", "path": "scripts/query.py", "description": "查询"},
    {"type": "ai", "description": "根据查询结果撰写报告", "prompt_hint": "先读 callback"},
    {"type": "script", "path": "scripts/publish.py", "description": "发布"},
]
ok = sf.apply_ai_pipeline_steps(legacy_root, lesson_id="exp_test3", goal="运行脚本并发布产物", pipeline_steps=steps_in)
check("apply 返回 True", ok)
p2 = sr.load_pipeline(legacy_root)
t2 = [s["type"] for s in (p2 or {}).get("steps", [])]
check("steps 保留 script+ai 混合顺序", t2 == ["script", "ai", "script"])
check("无 final_ai 字段", "final_ai" not in (p2 or {}))
check("ai_steps 兼容字段含该 ai 任务", [a.get("description") for a in (p2 or {}).get("ai_steps", [])] == ["根据查询结果撰写报告"])

print("== 7b. 交付目标但 AI 未给发布步骤 → 自动补发布步骤 ==")
root_pub = Path(tempfile.mkdtemp())
ensure_project_layout(root_pub)
sf.apply_ai_pipeline_steps(
    root_pub,
    lesson_id="exp_pub",
    goal="运行用户脚本并把产物放到 deliverables 交付物文件夹；不要校验",
    pipeline_steps=[
        {"type": "script", "path": "scripts/run.py", "description": "执行"},
        {"type": "ai", "description": "整理结果", "prompt_hint": "读 callback"},
    ],
)
ppub = sr.load_pipeline(root_pub)
tpub = [s["type"] for s in (ppub or {}).get("steps", [])]
check("补了发布步骤（script 尾部）", tpub == ["script", "ai", "script"] and any(
    str(s.get("tool") or "") == "publish" for s in (ppub or {}).get("steps", [])
))
check("发布脚本已落盘", any((root_pub / s["path"]).exists() for s in (ppub or {}).get("steps", []) if s.get("tool") == "publish"))
check("policy.invoke_ai_on_bad_data=False（不要校验）", (ppub or {}).get("policy", {}).get("invoke_ai_on_bad_data") is False)

print("== 8. run_pipeline_until_ai_or_end：脚本成功 → 0 Token 完成 ==")
from wokbee.engine.script_runner import run_pipeline_until_ai_or_end

proj = Path(tempfile.mkdtemp())
ensure_project_layout(proj)
sdir = scripts_dir(proj)
sdir.mkdir(parents=True, exist_ok=True)
(sdir / "ok_script.py").write_text(
    "import sys\nsys.stdout.reconfigure(encoding='utf-8')\nprint('hello')\n", encoding="utf-8"
)
data3 = {
    "version": 3,
    "goal": "测试",
    "steps": [
        {"id": "s1", "type": "script", "path": "scripts/ok_script.py", "tool": "script", "description": "运行"},
    ],
    "ai_steps": [],
}
(sdir / "pipeline.json").write_text(json.dumps(data3, ensure_ascii=False), encoding="utf-8")
r = run_pipeline_until_ai_or_end(proj, timeout_sec=60)
check("ok=True", r.ok)
check("need_ai=False（脚本全成功，不唤 AI）", not r.need_ai)
check("无 final_ai_task", getattr(r, "final_ai_task", None) is None)

print("== 9. run_pipeline_until_ai_or_end：脚本失败 → need_ai（异常恢复）==")
proj2 = Path(tempfile.mkdtemp())
ensure_project_layout(proj2)
sdir2 = scripts_dir(proj2)
sdir2.mkdir(parents=True, exist_ok=True)
(sdir2 / "bad_script.py").write_text(
    "import sys\nsys.stdout.reconfigure(encoding='utf-8')\nprint('脚本执行失败')\nsys.exit(1)\n", encoding="utf-8"
)
data4 = {
    "version": 3,
    "goal": "测试",
    "steps": [
        {"id": "s1", "type": "script", "path": "scripts/bad_script.py", "tool": "script", "description": "失败脚本"},
    ],
    "ai_steps": [],
}
(sdir2 / "pipeline.json").write_text(json.dumps(data4, ensure_ascii=False), encoding="utf-8")
r2 = run_pipeline_until_ai_or_end(proj2, timeout_sec=60)
check("ok=False", not r2.ok)
check("need_ai=True（交给 AI 恢复）", r2.need_ai)
check("error_summary 非空", bool(r2.error_summary))

print("== 10. 发布脚本实际执行：把 workspace 产物复制到 deliverables ==")
proj3 = Path(tempfile.mkdtemp())
ensure_project_layout(proj3)
(proj3 / "workspace").mkdir(parents=True, exist_ok=True)
(proj3 / "workspace" / "result.csv").write_text("a,b\n1,2\n", encoding="utf-8")
(proj3 / "workspace" / "script_callback_run.md").write_text("stdout", encoding="utf-8")
publish_script = sr.run_one_script(proj3, {"type": "script", "path": "scripts/pub.py", "tool": "publish"}) if False else None
# 直接把发布脚本写入 scripts/ 并运行
sdir3 = scripts_dir(proj3)
sdir3.mkdir(parents=True, exist_ok=True)
pub_src2 = sf._render_publish_script()
(sdir3 / "pub.py").write_text(pub_src2, encoding="utf-8")
res = sr.run_one_script(proj3, {"type": "script", "path": "scripts/pub.py", "tool": "publish", "id": "p1", "description": "发布"}, timeout_sec=60)
check("发布脚本执行成功", res.ok, res.error)
check("result.csv 已进入 deliverables", (proj3 / "deliverables" / "result.csv").exists())
check("callback 中间记录未进入 deliverables", not (proj3 / "deliverables" / "script_callback_run.md").exists())
check("未生成 final.md", not (proj3 / "deliverables" / "final.md").exists())

print()
print(f"结果：PASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
