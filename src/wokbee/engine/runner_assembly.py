"""Agent 装配前的项目准备、设计校验与模型解析。"""

from __future__ import annotations

from typing import Any

from tokbee.core.provider_store import ProviderStore, ResolvedModel

from wokbee.core.models import Project
from wokbee.core.paths import ensure_project_layout, workspace_sandbox
from wokbee.core.settings import WokBeeSettings
from wokbee.engine.runner_models import RunRequest
from wokbee.engine.runner_modes import mode_policy
from wokbee.engine.runner_sessions import ensure_experience_files


def prepare_project_root(request: RunRequest, mode: str) -> bool:
    """准备 Agent 的目录边界，返回是否为 DeziBee 设计模式。"""
    design_mode = mode_policy(mode).design_workspace
    if not design_mode:
        ensure_project_layout(request.project_root)
        workspace_sandbox(request.project_root).mkdir(parents=True, exist_ok=True)
        ensure_experience_files(request.project_root)
        return False
    request.project_root.mkdir(parents=True, exist_ok=True)
    for name in ("demo", "prd", "uploads"):
        (request.project_root / name).mkdir(parents=True, exist_ok=True)
    return True


def configure_design_write_validator(backend: Any, request: RunRequest, design_mode: bool) -> None:
    """仅设计模式校验 prototype 写入；校验失败仍保留内容供 Agent 补全。"""
    if not design_mode:
        return
    from dezibee.core.preview import validate_workbench_document

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


def resolve_model_for_project(
    project: Project,
    settings: WokBeeSettings,
    provider_store: ProviderStore | None = None,
) -> ResolvedModel:
    """按项目绑定、厂商默认、WokBee 默认和首个可用模型的顺序解析模型。"""
    store = provider_store or ProviderStore()
    provider = (project.provider or "").strip()
    model_id = (project.model_id or "").strip()
    if provider and model_id:
        resolved = store.resolve(provider, model_id)
        if resolved:
            return resolved
    default = store.resolve_default()
    if default:
        return default
    provider = (settings.default_provider or "").strip()
    model_id = (settings.default_model_id or "").strip()
    if provider and model_id:
        resolved = store.resolve(provider, model_id)
        if resolved:
            return resolved
    first = store.first_resolved()
    if not first:
        raise ValueError("没有可用模型，请先在「AI配置 → 厂商设置」中启用模型并填写 Key/Host。")
    return first
