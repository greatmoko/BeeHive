# WOKBEE 项目结构详细梳理文档

## 根目录文件
| 文件/目录 | 类型 | 说明 |
| --- | --- | --- |
| main.py | 主入口 | 项目启动的主程序文件 |
| pyproject.toml | 配置 | Python项目的构建、依赖等配置文件 |
| requirements.txt | 依赖 | 项目Python依赖包列表 |
| README.md | 文档 | 项目说明文档 |
| LICENSE | 协议 | 项目许可证文件 |
| .vscode/ | 配置 | VSCode编辑器的工作区配置 |
| scripts/ | 脚本 | 项目构建、测试相关脚本目录 |
| src/ | 源码 | 项目核心Python源码目录 |
| 启动WokBee.vbs | 启动脚本 | Windows系统下快速启动项目的VBS脚本 |

---

## 核心源码目录 `src/`
### 1. autobee 模块
自动化/连接器相关功能模块
| 文件路径 | 说明 |
| --- | --- |
| src/autobee/__init__.py | 模块初始化 |
| **autobee/core/** | 核心数据模块 |
| src/autobee/core/__init__.py | 子模块初始化 |
| src/autobee/core/models.py | 数据模型定义 |
| src/autobee/core/store.py | 数据存储逻辑 |
| **autobee/engine/** | 执行引擎模块 |
| src/autobee/engine/__init__.py | 子模块初始化 |
| src/autobee/engine/executor.py | 任务执行器 |
| src/autobee/engine/nl_builder.py | 自然语言构建器 |
| src/autobee/engine/scheduler.py | 任务调度器 |
| src/autobee/engine/wecom.py | 企业微信集成相关 |
| **autobee/ui/** | UI相关模块 |
| src/autobee/ui/__init__.py | 子模块初始化 |
| src/autobee/ui/autobee_view.py | 自动化视图组件 |

---

### 2. tokbee 模块
核心AI聊天/交互框架模块
| 文件路径 | 说明 |
| --- | --- |
| src/tokbee/__init__.py | 模块初始化 |
| src/tokbee/__main__.py | 模块直接运行入口 |
| src/tokbee/app.py | 应用主程序 |
| **tokbee/core/** | 核心逻辑模块 |
| src/tokbee/core/ai_client.py | AI客户端封装 |
| src/tokbee/core/ai_role.py | AI角色定义 |
| src/tokbee/core/chat_manager.py | 聊天会话管理器 |
| src/tokbee/core/config.py | 全局配置管理 |
| src/tokbee/core/context_manager.py | 对话上下文管理 |
| src/tokbee/core/errors.py | 自定义异常定义 |
| src/tokbee/core/file_reader.py | 文件读取工具 |
| src/tokbee/core/provider.py | AI服务提供者接口 |
| src/tokbee/core/provider_store.py | AI服务提供者存储 |
| src/tokbee/core/request_builder.py | 请求构建器 |
| src/tokbee/core/safe_io.py | 安全IO操作 |
| src/tokbee/core/services.py | 核心服务封装 |
| src/tokbee/core/session_settings.py | 会话设置管理 |
| src/tokbee/core/subprocess_util.py | 子进程工具 |
| **tokbee/ui/** | UI组件模块 |
| src/tokbee/ui/combo_style.py | 组合框样式自定义 |
| src/tokbee/ui/main_window.py | 主窗口实现 |
| src/tokbee/ui/no_wheel.py | 无滚动条组件 |
| **tokbee/ui/styles/** | UI样式管理 |
| src/tokbee/ui/styles/__init__.py | 样式子模块初始化 |
| src/tokbee/ui/styles/system.py | 系统样式定义 |
| src/tokbee/ui/styles/theme.py | 主题样式管理 |
| **tokbee/ui/viewmodels/** | MVVM视图模型 |
| src/tokbee/ui/viewmodels/__init__.py | 视图模型初始化 |
| src/tokbee/ui/viewmodels/chat_viewmodel.py | 聊天视图模型 |
| **tokbee/ui/views/** | UI视图实现 |
| src/tokbee/ui/views/automation_view.py | 自动化视图 |
| src/tokbee/ui/views/chat_view.py | 聊天主视图 |
| src/tokbee/ui/views/credential_view.py | 凭证管理视图 |
| src/tokbee/ui/views/provider_view.py | AI服务提供者视图 |
| src/tokbee/ui/views/session_defaults_view.py | 会话默认设置视图 |
| src/tokbee/ui/views/session_params_editor.py | 会话参数编辑器 |
| src/tokbee/ui/views/settings_view.py | 全局设置视图 |
| **tokbee/ui/widgets/** | 自定义UI组件 |
| src/tokbee/ui/widgets/__init__.py | 组件初始化 |
| src/tokbee/ui/widgets/context_ring.py | 上下文环组件 |
| **tokbee/utils/** | 工具模块 |
| src/tokbee/utils/logger.py | 日志工具类 |
| **tokbee/resources/** | UI资源目录** (非Python文件，包含图标、图片等) |

---

### 3. wokbee 模块 (主业务模块)
项目核心业务逻辑模块
| 文件路径 | 说明 |
| --- | --- |
| src/wokbee/__init__.py | 模块初始化 |
| **wokbee/core/** | 核心数据/配置管理 |
| src/wokbee/core/__init__.py | 子模块初始化 |
| src/wokbee/core/context_usage.py | 上下文使用统计 |
| src/wokbee/core/credential_crypto.py | 凭证加密工具 |
| src/wokbee/core/credential_store.py | 凭证存储管理 |
| src/wokbee/core/mcp_store.py | MCP（模型上下文协议）存储 |
| src/wokbee/core/models.py | 全局数据模型 |
| src/wokbee/core/paths.py | 项目路径管理 |
| src/wokbee/core/project_store.py | 项目配置存储 |
| src/wokbee/core/references.py | 引用/关联管理 |
| src/wokbee/core/settings.py | 全局设置管理 |
| src/wokbee/core/skills_store.py | 技能存储管理 |
| src/wokbee/core/timeline_format.py | 时间线格式定义 |
| **wokbee/engine/** | 业务核心引擎 |
| src/wokbee/engine/__init__.py | 子模块初始化 |
| src/wokbee/engine/access_coerce.py | 权限强制控制 |
| src/wokbee/engine/access_request.py | 权限请求处理 |
| src/wokbee/engine/agent_memory.py | 智能体记忆管理 |
| src/wokbee/engine/ai_errors.py | AI相关错误定义 |
| src/wokbee/engine/ai_throttle.py | AI请求节流/限速 |
| src/wokbee/engine/approval_policy.py | 审批策略管理 |
| src/wokbee/engine/archive_guard.py | 归档/守护逻辑 |
| src/wokbee/engine/ask_user.py | 用户交互询问模块 |
| src/wokbee/engine/autobee_tools.py | autobee工具集成 |
| src/wokbee/engine/cache_prefix.py | 缓存前缀管理 |
| src/wokbee/engine/cache_stats.py | 缓存统计 |
| src/wokbee/engine/chat_memory.py | 对话记忆管理 |
| src/wokbee/engine/credential_tools.py | 凭证相关工具 |
| src/wokbee/engine/deepseek_search.py | DeepSeek搜索集成 |
| src/wokbee/engine/intent_heuristics.py | 意图启发式分析 |
| src/wokbee/engine/lessons.py | 课程/教程模块 |
| src/wokbee/engine/model_factory.py | AI模型工厂 |
| src/wokbee/engine/network_tools.py | 网络工具 |
| src/wokbee/engine/project_tools.py | 项目相关工具 |
| src/wokbee/engine/prompt.py | 提示词工程 |
| src/wokbee/engine/readonly_backend.py | 只读后端逻辑 |
| src/wokbee/engine/runner.py | 任务运行器 |
| src/wokbee/engine/runtime_env.py | 运行时环境管理 |
| src/wokbee/engine/script_factory.py | 脚本工厂 |
| src/wokbee/engine/script_runner.py | 脚本运行器 |
| src/wokbee/engine/tool_truncate.py | 工具截断处理 |
| src/wokbee/engine/worker.py | 工作线程/进程管理 |
| **wokbee/gateway/** | 第三方网关集成 |
| src/wokbee/gateway/__init__.py | 子模块初始化 |
| src/wokbee/gateway/base.py | 网关基类 |
| src/wokbee/gateway/dispatcher.py | 网关请求分发 |
| src/wokbee/gateway/feishu.py | 飞书集成 |
| src/wokbee/gateway/manager.py | 网关管理器 |
| src/wokbee/gateway/provision.py | 网关配置管理 |
| src/wokbee/gateway/provision_wechat.py | 微信网关配置 |
| src/wokbee/gateway/router.py | 网关路由 |
| src/wokbee/gateway/store.py | 网关存储 |
| src/wokbee/gateway/wechat.py | 企业微信集成 |
| **wokbee/ui/** | 主业务UI |
| src/wokbee/ui/__init__.py | 子模块初始化 |
| src/wokbee/ui/action_bar.py | 操作栏组件 |
| src/wokbee/ui/ask_user_dialog.py | 用户询问对话框 |
| src/wokbee/ui/dialogs.py | 通用对话框 |
| src/wokbee/ui/gateway_workspace.py | 网关工作区视图 |
| src/wokbee/ui/mcp_workspace.py | MCP工作区视图 |
| src/wokbee/ui/settings_workspace.py | 设置工作区视图 |
| src/wokbee/ui/skills_workspace.py | 技能工作区视图 |
| src/wokbee/ui/timeline.py | 时间线组件 |
| src/wokbee/ui/web_chat.py | 网页版聊天视图 |
| src/wokbee/ui/wokbee_view.py | 主视图组件 |
| src/wokbee/ui/workspace.py | 工作区管理 |

---

## 脚本目录 `scripts/`
| 脚本文件 | 说明 |
| --- | --- |
| build_exe.ps1 | 将项目打包为Windows可执行文件的PowerShell脚本 |
| gw_ui_check.py | 网关UI功能检查脚本 |
| make_release.ps1 | 项目发布/构建版本脚本 |
| smoke_ai_throttle.py | AI节流功能冒烟测试脚本 |
| smoke_credential_vault.py | 凭证库功能冒烟测试脚本 |
| smoke_execute_timeout.py | 执行超时功能冒烟测试 |
| smoke_gateway.py | 网关功能冒烟测试 |
| smoke_tool_timeout.py | 工具超时功能冒烟测试 |

---

## 其他目录说明
- `.venv/`: Python虚拟环境目录（包含依赖包，无需修改源码）
- `.vscode/`: VSCode编辑器配置
- `__pycache__/`: Python编译后的字节码缓存（自动生成）
- `.dist-info/`: Python包的元数据文件（如依赖包信息）
