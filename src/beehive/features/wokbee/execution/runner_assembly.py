"""WokBee 项目准备与模型解析；设计业务由应用层单独装配。"""

from __future__ import annotations


from beehive.system.providers.provider_store import ProviderStore
from beehive.system.providers.provider_models import ResolvedModel

from beehive.features.wokbee.projects.models import Project
from beehive.features.wokbee.projects.paths import ensure_project_layout
from beehive.features.wokbee.settings import WokBeeSettings
from beehive.features.wokbee.execution.runner_models import RunRequest
from beehive.features.wokbee.execution.runner_modes import mode_policy
from beehive.features.wokbee.execution.runner_sessions import ensure_experience_files


def prepare_project_root(request: RunRequest, mode: str) -> bool:
    """准备 WokBee 项目目录，设计目录由 DeziBee 适配器负责。"""
    if mode_policy(mode).design_workspace:
        raise ValueError("设计目录请使用 DeziBee 的目录适配器")
    ensure_project_layout(request.project_root)
    ensure_experience_files(request.project_root)
    return False




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
