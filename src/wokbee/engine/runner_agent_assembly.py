"""Deep Agent 的工具、后端、上下文与审批装配。"""

from __future__ import annotations

import logging

from deepagents import FilesystemMiddleware, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend

from wokbee.core.models import _now
from wokbee.core.settings import WokBeeSettings
from wokbee.core.skills_store import SkillsStore
from wokbee.core.mcp_store import McpStore
from wokbee.engine.access_coerce import AccessCoerceBackend
from wokbee.engine.access_request import ApprovedDirRegistry, build_access_request_tool, mount_dir
from wokbee.engine.approval_policy import build_interrupt_on
from wokbee.engine.archive_guard import ArchiveDeniedBackend, attach_execute_watch
from wokbee.engine.ask_user import build_ask_user_tool
from wokbee.engine.autobee_tools import build_autobee_tools
from wokbee.engine.cache_prefix import (
    CacheHitTracker, PrefixGuard, build_session_context_block, prefix_fingerprint,
    sort_tools_by_name, static_system_prompt, tool_name_of, wrap_tools_truncate_results,
)
from wokbee.engine.credential_tools import build_credential_tools
from wokbee.engine.file_tools import FILESYSTEM_TOOL_DESCRIPTIONS, build_file_tools
from wokbee.engine.lessons import LessonStore, build_experience_tools
from wokbee.engine.model_factory import build_chat_model
from wokbee.engine.network_tools import NETWORK_TOOLS
from wokbee.engine.project_tools import build_project_meta_tools
from wokbee.engine.readonly_backend import ReadOnlyBackend
from wokbee.engine.runner_assembly import configure_design_write_validator, prepare_project_root
from wokbee.engine.runner_models import RunRequest
from wokbee.engine.runner_sessions import get_checkpointer as _get_checkpointer, remember_agent
from wokbee.engine.runtime_env import build_runtime_env_block
from wokbee.engine.script_runner import peek_pipeline

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
        design_mode = prepare_project_root(req, mode)

        # Windows：容忍 resolve() 偶发返回的 \\?\ 扩展路径，避免并发写文件时误判越界
        from wokbee.engine.backend_paths import install_extended_path_tolerance

        install_extended_path_tolerance()

        model = build_chat_model(
            req.resolved,
            timeout=self.settings.model_timeout_seconds,
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
        configure_design_write_validator(project_inner, req, design_mode)
        project_backend = project_inner
        interrupt_on = build_interrupt_on(req.approval)
        # 项目元信息工具（get_project_info/update_project_title/update_project_goal）始终免费：
        # 它们只读/写本项目名与目标，属低风险元数据操作，不在 build_interrupt_on 任何桶内，
        # 因此 interrupt_on 不会包含它们，无须 pop。

        # chat 与 run 共用项目 checkpointer，但 thread_id 不同，状态不串
        checkpointer = _get_checkpointer(req.project.id)

        skills_paths: list[str] = []
        routes: dict = {}
        skills_extra_lines: list[str] = []
        try:
            skills_store = SkillsStore()
            skills_store.cleanup_project_copies(req.project_root)
            skills_paths = skills_store.global_skills_paths()
            enabled_skills = [s.name for s in skills_store.list_enabled()]
            for prefix, skill_root in skills_store.skill_routes():
                # 只读挂载：全局技能库不是本项目产物，只允许读/列/搜，禁止写/改/删
                skills_inner = ReadOnlyBackend(
                    FilesystemBackend(
                        root_dir=str(skill_root),
                        virtual_mode=True,
                    )
                )
                routes[prefix] = skills_inner
                skills_extra_lines.append(
                    f"- Skills 目录（真实路径，execute 可用）：{skill_root}",
                )
                skills_extra_lines.append(
                    f"- 虚拟路径：{prefix}<技能名>/SKILL.md（只读，仅 read/ls/glob/grep）",
                )
                self._emit(
                    "info",
                    f"已挂载 Skills 目录（不复制到项目）：{skill_root}",
                )
            if enabled_skills:
                skills_extra_lines.append(
                    f"- 已启用 Skills：{', '.join(enabled_skills)}"
                )
        except Exception as e:
            logger.exception("加载 Skills 失败")
            self._emit("error", f"Skills 加载失败：{e}")

        # ---- 附加目录：预挂载全局白名单 + 供 request_access 动态挂载 ----
        access_registry = ApprovedDirRegistry()
        access_extra_lines: list[str] = []
        for entry in self.settings.additional_directories:
            prefix = mount_dir(
                routes,
                access_registry,
                entry["path"],
                name=entry.get("name") or "",
                persist=False,
                slug=entry.get("slug") or "",
                project_root=req.project_root,
            )
            if prefix:
                access_extra_lines.append(
                    f"- 附加目录虚拟路径：{prefix}（真实路径 {entry['path']}；read/write/grep 等文件工具可用，"
                    "请优先使用此虚拟路径；真实路径仅 execute 可用）"
                )
        if access_extra_lines:
            self._emit(
                "info", "已挂载附加目录：\n" + "\n".join(access_extra_lines)
            )
        if req.approval.bypass_sandbox:
            self._emit(
                "info",
                "已开启「忽略沙箱限制」：本会话所有工具免人工审批，"
                "execute 可操作任意真实路径（含项目外目录）。请审慎使用。",
            )

        # 始终用 CompositeBackend：即使初始无路由，request_access 也能往 routes 动态加 /ext/ 路由
        composite_backend = CompositeBackend(default=project_backend, routes=routes)
        backend = AccessCoerceBackend(
            composite_backend,
            project_root=req.project_root,
            registry=access_registry,
            allow_real_paths=bool(req.approval.bypass_sandbox),
        )
        attach_execute_watch(
            backend,
            cancel_event=self._cancel,
            default_timeout=float(self.settings.tool_timeout_seconds),
        )

        mcp_tools: list = []
        try:
            mcp_store = McpStore()
            if mcp_store.list_enabled():
                self._emit("info", "正在连接 MCP 服务器…")
                mcp_tools = mcp_store.load_tools()
                names = [getattr(t, "name", str(t)) for t in mcp_tools]
                if names:
                    self._emit(
                        "info",
                        f"已加载 MCP 工具：{', '.join(names[:20])}"
                        + (f" 等 {len(names)} 个" if len(names) > 20 else ""),
                    )
                    if not req.approval.skip_routine:
                        for n in names:
                            interrupt_on[str(n)] = True
                else:
                    self._emit("info", "MCP 已启用但未返回工具")
        except Exception as e:
            logger.exception("加载 MCP 失败")
            self._emit("error", f"MCP 加载失败：{e}")

        # 项目经验注入策略：
        # - run/chat 模式自动注入项目经验；design 模式跳过经验管线。
        # - 经验只来自当前项目的 memory/experiences/，不读取全局或对话记忆。
        pipe_probe = peek_pipeline(req.project_root)
        first_run = mode != "run" or not pipe_probe.ran or not pipe_probe.steps
        lesson_store = None if design_mode else LessonStore(req.project_root)

        experience_digest = "" if design_mode else lesson_store.prompt_digest()
        if not design_mode and not lesson_store.is_empty():
            latest = lesson_store.latest_path()
            if first_run:
                self._emit(
                    "info",
                    f"首次运行（执行管线为空），已注入最新项目经验："
                    f"{latest.name if latest else 'experiences/'}"
                    f"（历史经验不注入；禁止使用 archives/）",
                )
            else:
                self._emit(
                    "info",
                    f"非首次运行（已有执行管线），仍自动注入项目经验："
                    f"{latest.name if latest else 'experiences/'}"
                    ".",
                )

        # Reasonix ImmutablePrefix：system 静态；易变态进【会话上下文】user 块
        system_prompt = static_system_prompt(mode=mode)
        runtime_env_block = build_runtime_env_block(
            project_root=str(req.project_root),
            model=f"{req.resolved.provider_name}/{req.resolved.model_id}",
            policy=req.approval.summary(),
            settings=self.settings,
            design_mode=(mode == "design"),
        )
        context_extra: list[str] = list(skills_extra_lines) + access_extra_lines
        if mode == "run":
            context_extra = [f"用户于 {_now()} 点击运行。"] + context_extra
        self._session_context_block = build_session_context_block(
            title=req.project.title,
            goal=req.project.goal or "",
            approval_summary=req.approval.summary(),
            max_steps=req.max_steps if mode != "chat" else None,
            experience_digest=experience_digest,
            mode=mode,
            runtime_env_block=runtime_env_block,
            extra_lines=context_extra or None,
        )

        project_tools = build_project_meta_tools(
            project_id=req.project.id,
            settings=self.settings,
            emit=self._emit,
        )
        # DeepSeek 服务端搜索：包成工具给 Agent 用（开关在设置 enable_deepseek_search；
        # 需官方 DeepSeek Key 才真正注册，主模型可是本地模型）。
        deepseek_search = None
        if getattr(self.settings, "enable_deepseek_search", True):
            try:
                from wokbee.engine.deepseek_search import build_deepseek_search_tool

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
            + list(build_file_tools(backend=backend, emit=self._emit))
            + list(project_tools)
            + list(build_credential_tools())
            + (
                []
                if design_mode
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
            + list(build_autobee_tools(
                resolved=req.resolved,
                provider_store=self.provider_store,
                emit=self._emit,
            ))
            + list(mcp_tools)
        )
        tools = wrap_tools_truncate_results(
            tools,
            project_root=req.project_root,
            tool_timeout=self.settings.tool_timeout_seconds,
            max_parallel_tools=self.settings.max_parallel_tools,
        )
        tool_names = [tool_name_of(t) for t in tools]
        fp = prefix_fingerprint(system_prompt, tool_names)

        def _on_cache_update(payload: dict) -> None:
            phase = payload.get("phase")
            if phase == "pin":
                self._emit(
                    "info",
                    f"缓存前缀已钉死（DeepSeek prefix-cache）：fp={payload.get('prefix_fp')}，"
                    f"tools={payload.get('tool_count')}。"
                    " system 本会话不变；项目态在用户消息【会话上下文】。",
                    {"cache": True, **payload},
                )
                return
            tag = self._cache_tracker.format_tag()
            self._emit(
                "cache",
                tag,
                {"cache": True, **payload},
            )

        self._cache_tracker = CacheHitTracker(on_update=_on_cache_update)
        self._cache_tracker.note_prefix(fp, len(tool_names))

        # Reasonix 前缀护栏：只对 append-only 破坏告警，正常追加不打扰。
        def _on_prefix_drift(payload: dict) -> None:
            drift = payload.get("drift")
            if not isinstance(drift, dict):
                # 非漂移载荷（如发现点信息）不进改写告警，避免误报。
                return
            self._emit(
                "error",
                "缓存前缀被改写（append-only 破坏，DeepSeek 前缀缓存将在该点失效）：\n"
                f"位置 #{drift.get('index')} 类型 {drift.get('kind') or 'rewrite'} "
                f"role={drift.get('role') or '?'}\n"
                f"内容：{drift.get('content') or '（空）'}",
                {"cache": True, **payload},
            )

        self._prefix_guard = PrefixGuard(on_drift=_on_prefix_drift)
        self._prefix_guard.note_static(fp, len(tool_names))

        # ask_user 在工具内 interrupt，绝不能再套一层 interrupt_on
        interrupt_on.pop("ask_user", None)

        agent_name = (
            f"wokbee-chat-{req.project.id}"
            if mode == "chat"
            else f"wokbee-{req.project.id}"
        )
        agent = create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            backend=backend,
            # 覆盖 Deep Agents 的通用文件工具说明：项目使用虚拟路径，并提供无需临时脚本的
            # 定位 → 小范围读取 → 按行/锚点编辑流程。与默认中间件同名，create_deep_agent 会原位替换。
            middleware=[
                FilesystemMiddleware(
                    backend=backend,
                    custom_tool_descriptions=FILESYSTEM_TOOL_DESCRIPTIONS,
                )
            ],
            interrupt_on=interrupt_on or None,
            # 经验只注入首条 user 的【会话上下文】，不使用外部 MemoryMiddleware，
            # 避免每次请求重新加载经验进 system，保持前缀缓存稳定。
            skills=skills_paths or None,
            checkpointer=checkpointer,
            name=agent_name,
        )
        if mode == "run":
            remember_agent(req.project.id, agent)
        return agent
