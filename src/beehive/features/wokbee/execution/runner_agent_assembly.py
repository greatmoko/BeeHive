"""Deep Agent 的工具、后端、上下文与审批装配。"""

from __future__ import annotations

import logging
import sqlite3

from deepagents import FilesystemMiddleware, create_deep_agent

from beehive.system.agent.access_request import build_access_request_tool
from beehive.features.wokbee.execution.approval_policy import build_interrupt_on
from beehive.features.wokbee.execution.archive_guard import ArchiveDeniedBackend
from beehive.system.agent.ask_user import build_ask_user_tool
from beehive.features.wokbee.execution.runner_mounts import mount_capabilities
from beehive.features.wokbee.execution.runner_cache_events import configure_cache_events
from beehive.features.wokbee.execution.prompt import build_session_context_block
from beehive.system.agent.tool_truncate import prefix_fingerprint
from beehive.system.agent.tool_truncate import sort_tools_by_name
from beehive.system.agent.tool_truncate import tool_name_of
from beehive.system.agent.tool_truncate import wrap_tools_runtime_controls
from beehive.system.prompts import static_system_prompt
from beehive.system.agent.credential_tools import build_credential_tools
from beehive.system.agent.file_tools import (
    DESIGN_READ_FILE_SOFT_LIMIT,
    FILESYSTEM_TOOL_DESCRIPTIONS,
    READ_FILE_SOFT_LIMIT,
    build_file_tools,
    wrap_read_file_soft_limit,
)
from beehive.features.wokbee.experience.lesson_storage import LessonStore
from beehive.features.wokbee.experience.lesson_tools import build_experience_tools
from beehive.system.agent.model_factory import build_chat_model
from beehive.system.agent.network_tools import NETWORK_TOOLS
from beehive.features.wokbee.execution.project_tools import build_project_meta_tools
from beehive.features.wokbee.execution.runner_assembly import prepare_project_root
from beehive.features.wokbee.execution.runner_models import RunRequest
from beehive.features.wokbee.execution.runner_modes import mode_label, mode_policy
from beehive.features.wokbee.projects.paths import agent_rules_path
from beehive.features.wokbee.execution.runner_sessions import get_checkpointer as _get_checkpointer, remember_agent
from beehive.system.runtime.runtime_env_format import build_runtime_env_settings_text
from beehive.system.runtime.runtime_env_cache import ensure_runtime_env

logger = logging.getLogger("wokbee")


class AgentAssemblyMixin:
    def build_agent(self, req: RunRequest, *, mode: str = "run"):
        """构建完整能力 Agent。

        mode=run：按经验管线推进项目目标。
        mode=chat：同等完整能力（文件/联网/execute/MCP/Skills），但不跑经验管线；
                   提问可与目标无关，并可改项目名称/目标。
        mode=design：DeziBee 设计模式——同样不跑经验管线，且不注入项目经验，
                   不挂项目经验工具；保留 Skills 与当前会话能力。
        """
        policy = mode_policy(mode)
        if policy.design_workspace:
            if self.design_prepare_root is None:
                raise RuntimeError("设计运行缺少目录适配，请通过应用运行器工厂创建")
            self.design_prepare_root(req)
        else:
            prepare_project_root(req, policy.name)

        # Windows：容忍 resolve() 偶发返回的 \\?\ 扩展路径，避免并发写文件时误判越界
        from beehive.system.agent.backend_paths import install_extended_path_tolerance

        install_extended_path_tolerance()

        model = build_chat_model(
            req.resolved,
            timeout=self.settings.model_timeout_seconds,
        )
        from beehive.system.memory.store import MemoryStore
        from beehive.system.memory.session import SessionMemory
        from beehive.system.memory.agent_tools import (
            build_memory_tools,
        )
        from beehive.system.memory.context.session_middleware import SessionMemoryMiddleware

        self._memory_model = model
        self._memory_store = MemoryStore()
        self._memory_reads = getattr(self, "_memory_reads", {})
        session_memory = SessionMemory(req.project_root)
        memory_tools = build_memory_tools(
            session_memory, self._memory_store, self._memory_reads,
            memory_state=getattr(self, "_memory_notifications", None),
        )
        project_inner = ArchiveDeniedBackend(
            root_dir=str(req.project_root),
            virtual_mode=True,
            # 单工具执行超时：execute 子进程在内会按此阈值被杀掉（从 180s 固定值改为可配置）
            timeout=int(self.settings.tool_timeout_seconds),
            inherit_env=True,
        )
        # 暂停按钮与工具超时共用：execute 轮询此 Event，可在命令执行中途杀进程树
        project_inner.cancel_event = self._cancel
        if policy.design_workspace:
            if self.design_write_validator is None:
                raise RuntimeError("设计运行缺少写入校验，请通过应用运行器工厂创建")
            self.design_write_validator(project_inner, req, True)
        project_backend = project_inner
        interrupt_on = build_interrupt_on(req.approval)
        # 项目元信息工具（get_project_info/update_project_title/update_project_goal）始终免费：
        # 它们只读/写本项目名与目标，属低风险元数据操作，不在 build_interrupt_on 任何桶内，
        # 因此 interrupt_on 不会包含它们，无须 pop。

        # chat 与 run 共用项目 checkpointer，但 thread_id 不同，状态不串
        checkpointer = _get_checkpointer(req.project.id)

        (
            backend, composite_backend, access_registry, skills_paths,
            skills_extra_lines, access_extra_lines, mcp_tools,
        ) = mount_capabilities(self, req, project_backend, interrupt_on)

        # 项目经验注入策略：
        # - run/chat 模式自动注入项目经验；design 模式跳过经验流程。
        # - 经验只来自当前项目的 memory/，不读取全局或对话记忆。
        # 首次判定：memory/ 下存在最新经验文件（exp_*.md）即为非首次运行，与管线无关。
        lesson_store = LessonStore(req.project_root) if policy.experience else None
        first_run = lesson_store is None or lesson_store.is_empty()
        self._mode_label = mode_label(mode, first_run=first_run)

        experience_digest = lesson_store.prompt_digest() if lesson_store is not None else ""
        if lesson_store is not None:
            latest = lesson_store.latest_path()
            if first_run:
                self._emit(
                    "info",
                    "首次运行（memory/ 下尚无经验文件），将从零开始执行目标；"
                    "收尾请固化本次验证过的流程与可复用脚本。",
                )
            else:
                self._emit(
                    "info",
                    f"非首次运行：已检测到最新经验 {latest.name if latest else 'memory/exp_*.md'}"
                    "，请按经验记载的成功路径调取并运行 memory/ 下的脚本，再推进剩余任务。",
                )

        # Reasonix ImmutablePrefix：system 静态；易变态进【会话上下文】user 块
        system_prompt = static_system_prompt(mode=mode)
        # 环境信息只保存到全局记忆；会话由全局记忆快照统一注入，不单独加载环境块。
        ensure_runtime_env(self.settings)
        try:
            self._memory_store.ensure_environment(
                build_runtime_env_settings_text(self.settings)
            )
        except (OSError, ValueError, sqlite3.Error) as exc:
            logger.warning("首次环境信息写入全局记忆失败，继续使用当前记忆：%s", exc)
        self._memory_global = self._memory_store.global_text(self._memory_store.global_memory())
        context_extra: list[str] = list(skills_extra_lines) + access_extra_lines
        # 用户自定义项目规则（.project/Agent.md）：每次构建 agent 时读取一次；
        # 文件为空或不存在则跳过，不修改文件。内容脱敏后注入会话上下文。
        try:
            _rules_path = agent_rules_path(req.project_root)
            agent_rules_text = (
                _rules_path.read_text(encoding="utf-8").strip()
                if _rules_path.is_file()
                else ""
            )
        except OSError:
            agent_rules_text = ""
        if agent_rules_text:
            from beehive.system.credentials.credential_store import redact_text

            context_extra.append(
                "【用户自定义项目规则】（来自 .project/Agent.md，用户自定义，优先遵守）\n"
                + redact_text(agent_rules_text)
            )
        self._session_context_block = build_session_context_block(
            title=req.project.title,
            goal=req.project.goal or "",
            approval_summary=req.approval.summary(),
            max_steps=req.max_steps if policy.pipeline else None,
            experience_digest=experience_digest,
            mode=mode,
            first_run=first_run,
            project_id=req.project.id,
            project_root=str(req.project_root),
            extra_lines=context_extra or None,
        )
        project_tools = build_project_meta_tools(
            project_id=req.project.id,
            settings=self.settings,
            emit=self._emit,
        ) if policy.project_metadata else []
        # DeepSeek 服务端搜索：包成工具给 Agent 用（开关在设置 enable_deepseek_search；
        # 需官方 DeepSeek Key 才真正注册，主模型可是本地模型）。
        deepseek_search = None
        if getattr(self.settings, "enable_deepseek_search", True):
            try:
                from beehive.system.agent.deepseek_search import build_deepseek_search_tool

                has_ds_key = bool(
                    getattr(
                        self.provider_store.get_settings("deepseek"),
                        "api_key",
                        "",
                    ).strip()
                )
                if has_ds_key:
                    deepseek_search = build_deepseek_search_tool(self.provider_store)
                    self._emit(
                        "info",
                        "已挂载 DeepSeek 服务端搜索工具：deepseek_web_search（检索质量更高，多轮+引用）。",
                    )
                else:
                    self._emit(
                        "info",
                        "已开启 DeepSeek 服务端搜索，但未配置官方 DeepSeek 的 API Key；"
                        "deepseek_web_search 暂不生效，去「厂商设置」填官方 Key 即可。",
                    )
            except Exception:
                logger.exception("构建 DeepSeek 搜索工具失败")
                deepseek_search = None

        tools = sort_tools_by_name(
            list(NETWORK_TOOLS)
            + memory_tools
            + list(build_file_tools(backend=backend, emit=self._emit))
            + list(project_tools)
            + list(build_credential_tools())
            + (
                []
                if not policy.experience
                else list(build_experience_tools(
                    project_id=req.project.id,
                    project_root=req.project_root,
                    goal=req.project.goal or req.user_message,
                    model_label=f"{req.resolved.provider_name}/{req.resolved.model_id}",
                    policy=req.approval.summary(),
                    emit=self._emit,
                    on_written=self._mark_experience_updated,
                    events_provider=self._snapshot_run_events,
                ))
            )
            + [build_ask_user_tool()]
            + [build_access_request_tool(
                composite_backend, access_registry, self.settings,
                emit=self._emit, project_root=req.project_root,
            )]
            + ([deepseek_search] if deepseek_search is not None else [])
            + (list(self.task_tools_factory(
                resolved=req.resolved, provider_store=self.provider_store, emit=self._emit,
            )) if self.task_tools_factory is not None else [])
            + list(mcp_tools)
        )
        tools = wrap_tools_runtime_controls(
            tools,
            tool_timeout=self.settings.tool_timeout_seconds,
            max_parallel_tools=self.settings.max_parallel_tools,
        )
        tool_names = [tool_name_of(t) for t in tools]
        fp = prefix_fingerprint(system_prompt, tool_names)

        configure_cache_events(self, fp, tool_names)

        # ask_user 在工具内 interrupt，绝不能再套一层 interrupt_on
        interrupt_on.pop("ask_user", None)

        agent_name = (
            f"wokbee-chat-{req.project.id}"
            if not policy.pipeline
            else f"wokbee-{req.project.id}"
        )
        filesystem_middleware = FilesystemMiddleware(
            backend=backend,
            custom_tool_descriptions=FILESYSTEM_TOOL_DESCRIPTIONS,
        )
        wrap_read_file_soft_limit(
            filesystem_middleware,
            max_chars=(
                DESIGN_READ_FILE_SOFT_LIMIT
                if policy.design_workspace
                else READ_FILE_SOFT_LIMIT
            ),
        )
        agent = create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            backend=backend,
            # 覆盖 Deep Agents 的通用文件工具说明：项目使用虚拟路径，并提供无需临时脚本的
            # 定位 → 小范围读取 → 按行/锚点编辑流程。与默认中间件同名，create_deep_agent 会原位替换。
            middleware=[filesystem_middleware, SessionMemoryMiddleware(
                model, backend=backend, session=session_memory, store=self._memory_store,
                context_window=getattr(req.resolved, "context_window", 0), emit=self._emit,
            )],
            interrupt_on=interrupt_on or None,
            # 经验只注入首条 user 的【会话上下文】，不使用外部 MemoryMiddleware，
            # 避免每次请求重新加载经验进 system，保持前缀缓存稳定。
            skills=skills_paths or None,
            checkpointer=checkpointer,
            name=agent_name,
        )
        if policy.pipeline:
            remember_agent(req.project.id, agent)
        return agent
