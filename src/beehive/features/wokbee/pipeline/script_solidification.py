"""根据运行轨迹固化可复用脚本到 memory/。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from beehive.common.io.safe_io import safe_write_text
from beehive.features.wokbee.pipeline.script_models import ScriptStep, SolidifyResult


from beehive.features.wokbee.projects.paths import ensure_project_layout, scripts_dir
from beehive.features.wokbee.pipeline.script_events import extract_scriptable_from_events, extract_scriptable_from_path_text
from beehive.features.wokbee.pipeline.script_commands import _project_script_path_from_command, _script_tokens
from beehive.features.wokbee.pipeline.script_render import _render_script
from beehive.features.wokbee.pipeline.script_names import make_script_name

def solidify_scripts(
    project_root: Path,
    *,
    lesson_id: str,
    goal: str = "",
    summary: str = "",
    success_path: str = "",
    events: list | None = None,
) -> SolidifyResult:
    """根据轨迹固化脚本：从执行事件与成功路径中提取真实跑过的命令，
    渲染成脚本写入 memory/；不再生成 pipeline.json。"""

    ensure_project_layout(project_root)
    sdir = scripts_dir(project_root)
    sdir.mkdir(parents=True, exist_ok=True)

    scriptable = extract_scriptable_from_events(events or [])
    from_path = extract_scriptable_from_path_text(success_path)
    # 合并：事件优先，路径轨迹补漏（避免 AI 散文覆盖后丢工具）
    seen_keys = {
        f"{s.tool}:{json.dumps(s.args, ensure_ascii=False, sort_keys=True)}"
        for s in scriptable
    }
    for s in from_path:
        key = f"{s.tool}:{json.dumps(s.args, ensure_ascii=False, sort_keys=True)}"
        if key not in seen_keys:
            seen_keys.add(key)
            scriptable.append(s)

    written: list[ScriptStep] = []
    project_id = Path(project_root).name

    # 内容指纹 → 已有脚本：同参数重复固化（同轮修正/后续轮重跑）时直接复用，不再写重复脚本。
    # 指纹按换行符/BOM 归一化，避免 CRLF/LF 差异导致误判不重复。
    content_index: dict[str, str] = {}
    for p in sorted(sdir.glob("*")):
        if p.is_file() and p.suffix.lower() in {".py", ".bat", ".cmd", ".ps1", ".sh", ".js"}:
            try:
                text = p.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
            except (OSError, UnicodeDecodeError):
                continue
            content_index.setdefault(hashlib.sha256(text.encode("utf-8")).hexdigest(), f"memory/{p.name}")

    def _unique_fname(purpose: str, ext: str = ".py") -> str:
        """规范命名 + 同秒同名冲突时追加序号，绝不覆盖已有脚本。"""
        base = make_script_name(project_id, purpose, ext)
        cand = sdir / base
        n = 2
        while cand.exists():
            stem = Path(base).stem
            cand = sdir / f"{stem}_{n}{ext}"
            n += 1
        return cand.name

    direct_upload_paths: set[str] = set()
    for step in scriptable:
        if step.tool == "execute":
            direct = _project_script_path_from_command(
                project_root,
                str((step.args or {}).get("command") or ""),
                "uploads",
            )
            if direct:
                direct_upload_paths.add(direct.lower())

    for step in scriptable:
        if step.tool == "execute":
            command = str((step.args or {}).get("command") or "")
            direct = _project_script_path_from_command(project_root, command, "uploads")
            if direct:
                if direct.lower() in {s.rel_path.lower() for s in written}:
                    continue
                step.rel_path = direct
                step.args = {"source": "uploaded_script", "path": direct}
                step.description = f"直接运行上传脚本：{direct}"
                written.append(step)
                continue

            generated = _project_script_path_from_command(project_root, command, "memory")
            if generated:
                # 命令引用的 memory/ 脚本已存在（如收尾自测/后续轮复用）：直接引用，不套娃新脚本。
                step.rel_path = generated
                step.description = f"直接执行既有脚本：{generated}"
                written.append(step)
                continue
            has_memory_ref = any(
                "/memory/" in token.replace("\\", "/").lower()
                or token.replace("\\", "/").lower().startswith("memory/")
                for token in _script_tokens(command)
            )
            if direct_upload_paths and (generated or has_memory_ref):
                # 已识别到上传脚本时，忽略旧的 memory/ 包装脚本，避免脚本套娃。
                continue

        src = _render_script(step)
        if not src:
            continue
        digest = hashlib.sha256(src.replace("\r\n", "\n").encode("utf-8")).hexdigest()
        if digest in content_index:
            # 同参数脚本已存在（同轮重考/后续轮重跑）：复用既有地址，不写重复文件。
            step.rel_path = content_index[digest]
            written.append(step)
            continue
        if step.tool == "execute":
            label = str((step.args or {}).get("label") or "execute")
        else:
            label = step.tool
        fname = _unique_fname(label)
        path = sdir / fname
        safe_write_text(path, src)
        step.rel_path = f"memory/{fname}"
        content_index[digest] = step.rel_path
        written.append(step)

    if written:
        script_md = "\n".join(
            f"- `{s.rel_path}` — {s.description}" for s in written
        )
        script_md += (
            "\n\n约定：每个脚本执行后把 callback 写入 "
            "`script_callback_<脚本名>.md`（项目根目录）；"
            "需要交付的文件复制到 output/（保留原始文件，不合并成 final.md）。"
        )
    else:
        script_md = "（本轮无可固化脚本。）"

    return SolidifyResult(
        script_steps=written,
        script_section_md=script_md,
    )
