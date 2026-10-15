"""Agent 的只读技能、授权目录、MCP 和执行监视装配。"""
from __future__ import annotations
import logging
from deepagents.backends import CompositeBackend, FilesystemBackend
from beehive.system.skills.skills_store import SkillsStore
from beehive.system.mcp.mcp_store import McpStore
from beehive.system.agent.access_coerce import AccessCoerceBackend
from beehive.system.agent.access_request import ApprovedDirRegistry, mount_dir
from beehive.features.wokbee.execution.archive_guard import attach_execute_watch
from beehive.system.agent.readonly_backend import ReadOnlyBackend

logger = logging.getLogger("wokbee")

def mount_capabilities(self, req, project_backend, interrupt_on):
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

    return backend, composite_backend, access_registry, skills_paths, skills_extra_lines, access_extra_lines, mcp_tools
