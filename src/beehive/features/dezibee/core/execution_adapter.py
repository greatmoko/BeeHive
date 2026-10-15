"""设计执行专用目录、原型校验和需求上下文版本适配。"""
from __future__ import annotations
from typing import Any
from hashlib import sha256

def prepare_design_root(request):
    request.project_root.mkdir(parents=True, exist_ok=True)
    for name in ("demo", "prd", "uploads"):
        (request.project_root / name).mkdir(parents=True, exist_ok=True)
    return True

def configure_design_write_validator(backend: Any, request: RunRequest, design_mode: bool) -> None:
    """仅设计模式校验 prototype 写入；校验失败仍保留内容供 Agent 补全。"""
    if not design_mode:
        return
    from beehive.features.dezibee.core.workbench_syntax import validate_workbench_document

    prototype_path = (request.project_root / "demo" / "index.html").resolve()

    def validate_design_write(path, content):
        if path != prototype_path:
            return None
        try:
            validate_workbench_document(content)
        except ValueError as exc:
            detail = str(exc).replace("，拒绝覆盖原型", "").replace("拒绝覆盖原型。", "")
            return f"WORKBENCH_DATA 可能不完整：{detail}。文件已写入，请继续读取当前文件并补全后再校验。"
        return None

    backend.write_validator = validate_design_write

def prepare_design_question(question, context, values, has_history):
    """返回本轮文本、checkpoint 元数据、需显示的上下文更新。"""
    if not context:
        return question, {}, ""
    version = sha256(context.encode("utf-8")).hexdigest()
    messages = (values.get("messages") or []) if isinstance(values, dict) else []
    previous = None
    for message in reversed(messages):
        metadata = (
            message.get("additional_kwargs", {}) if isinstance(message, dict)
            else getattr(message, "additional_kwargs", {})
        )
        if "design_context" in metadata:
            previous = metadata["design_context"]
            break
    update = ""
    if not has_history:
        question = f"{context}\n\n【本轮指令】\n{question}"
    elif previous != version:
        # 旧记录或压缩后缺少版本标记时，显式同步当前版本。
        update = (
            "【需求上下文已更新】以下为当前版本，替代此前的需求上下文。\n"
            + context.replace("【需求上下文】\n", "", 1).replace(
                "需求描述：", "需求描述已更新为：", 1
            )
        )
        question = f"{update}\n\n【本轮指令】\n{question}"
    return question, {"design_context": version}, update
