# WokBee 当前运行过程与优化观察

> 基于当前工作区代码整理，代码观察日期：2026-09-16。
> 目标：把 WokBee 从启动到一次运行结束的真实链路摊开，便于逐段检查和决定优化优先级。

## 1. 一句话结论

WokBee 当前是一个“桌面 UI + 后台线程 + 进程内 AgentRunner + 项目目录状态机”的系统：

```text
输入入口
  ├─ 桌面「运行」
  ├─ 桌面「发送」（交互模式）
  ├─ AutoBee 定时任务
  └─ 微信 / 飞书消息网关
        ↓
按 project_id 串行化
        ↓
解析模型 → 构建 Agent → 加载经验 / pipeline / 工具
        ↓
脚本阶段自动执行；AI 阶段调用模型；需要时进入审批/澄清中断
        ↓
事件写入 runs/events.jsonl，同时增量刷新桌面时间线
        ↓
成功 / 失败 / 取消 → 更新 project.json
        ↓
首次运行或异常时补写经验，固化 scripts/ 与 pipeline.json
```

核心设计是：第一次主要由 AI 探路，之后优先按 `scripts/pipeline.json` 重跑确定性步骤；脚本不耗 Token，固定的 `ai` 步骤仍然会调用模型，但不重新规划整条流程。

## 2. 组件职责

| 层 | 主要文件 | 当前职责 |
|---|---|---|
| 应用入口 | `main.py`、`src/tokbee/app.py` | 建立 Qt 应用、主窗口、日志、崩溃捕获、运行环境后台探测、引擎预热 |
| 服务注册 | `src/tokbee/core/services.py` | 在应用生命周期内复用 `WokBeeSettings`、`ProjectStore`、AutoBee、Gateway 等实例 |
| WokBee UI | `src/wokbee/ui/wokbee_view.py`、`workspace.py` | 项目侧栏、项目工作区、运行/发送/暂停/审批、时间线刷新 |
| 项目状态 | `src/wokbee/core/models.py`、`project_store.py` | 项目元数据、状态、事件追加、项目目录、归档与回收站 |
| 运行线程 | `src/wokbee/engine/worker.py` | 把同步 `AgentRunner` 放进 `QThread`，转发事件并接收 UI 的取消/审批结果 |
| Agent 引擎 | `src/wokbee/engine/runner.py` | 构建 Deep Agents、注入上下文、运行 pipeline、流式事件、处理中断、写经验 |
| 本地管线 | `src/wokbee/engine/script_runner.py` | 读取 `scripts/pipeline.json`，执行脚本阶段，遇到 AI 阶段停下交给 Runner |
| 经验系统 | `src/wokbee/engine/lessons.py`、`script_factory.py` | 读取最新经验、总结本轮、固化脚本、合并并写回 pipeline |
| 外部入口 | `src/autobee/engine/executor.py`、`src/wokbee/gateway/*` | 定时任务和手机消息复用同一个 Runner，但各自有入口适配逻辑 |

## 3. 应用启动阶段

### 3.1 启动顺序

1. `main.py` 把 `src/` 加入 Python 路径，初始化日志和崩溃捕获。
2. 创建 `tokbee.app.Application`。
3. `Application.__init__` 创建全局 `Config`、主题、Qt 应用和 `MainWindow`。
4. `MainWindow` 创建 `ServiceRegistry`，注册 TokBee、WokBee、AutoBee、DeziBee、AIConfig 等页面。
5. WokBee 页面使用同一个 `ProjectStore` 和 `WokBeeSettings`；首次真正显示 WokBee 页面时再加载项目列表和首个项目。
6. 主窗口注册并启动 AutoBee 调度器、消息网关。
7. 窗口显示后，后台启动 Deep Agents / LangChain / LangGraph 预热线程；预热失败时，首次实际运行仍会按需导入。
8. `ProjectStore` 初始化默认工作区、`_index.json`，并清理超过 7 天的 `_trash` 项目。

### 3.2 启动时的并发原则

- Qt 主线程负责窗口和页面，不在启动关键路径同步导入重型 Agent 栈。
- 引擎预热、运行 Agent、总结经验、上下文压缩、AI 改名分别放在后台线程。
- 服务实例由 `ServiceRegistry` 统一创建，避免 UI 页面各自持有不一致的 Store / Scheduler / Gateway。

## 4. 项目创建与加载

### 4.1 创建项目

桌面点击“新建项目”时：

1. `ProjectStore.create()` 生成 `prj_<12 位 uuid>`。
2. 项目标题清理并截断到最多 15 字；目标可为空。
3. 审批策略复制当前全局设置，之后项目独立保存。
4. 模型绑定优先级：厂商设置中标记为默认的模型 → WokBee 设置默认模型 → 留空，运行时再解析。
5. `save()` 创建项目目录、写入 `project.json`，并更新工作区 `_index.json`。
6. 追加一条“项目已创建”事件到 `runs/events.jsonl`。
7. UI 创建对应的 `_ProjectWorkspace`，加载项目要素和最近事件。

### 4.2 项目目录

默认根目录是 `~/WokBeeWorkspace/`，每个项目使用自己的目录：

```text
WokBeeWorkspace/
├── _index.json                         # 项目索引
├── _trash/                             # 删除后的回收站，默认保留 7 天
└── prj_xxx/
    ├── project.json                    # 项目名称、目标、状态、模型、审批策略
    ├── runs/events.jsonl               # 事件时间线
    ├── memory/AGENTS.md                # 项目标识与经验使用约定
    ├── memory/experiences/             # 多份经验，运行时只读最新一份
    ├── memory/context_state.json       # UI 上下文压缩点
    ├── scripts/pipeline.json           # 有序 script/ai 步骤
    ├── scripts/*                       # 可复用本地脚本，不参与普通归档
    ├── workspace/                      # 中间文件、脚本 callback
    ├── deliverables/                   # 最终交付物
    ├── uploads/                        # 用户上传文件，长期保留
    ├── uploads/references/             # 参考材料与使用过的 Skill 快照
    └── archives/                       # 会话归档快照，Agent 禁止访问
```

切换项目时只隐藏旧工作区，不销毁它；每个项目的 Worker 可以继续在后台运行。时间线首次只实例化最近 15 条，上翻时每次再加载 50 条。

## 5. 桌面「运行」主链路

### 5.1 UI 侧前置处理

入口：`src/wokbee/ui/workspace.py::_on_run()`。

1. 校验当前项目存在，且没有运行中的 Agent、总结经验、压缩上下文或 AI 更新项目信息。
2. 从操作栏取出输入文本和附件。
3. 如果项目目标为空，弹窗要求用户补填；填入的文本直接成为项目目标，不再重复作为运行指令。
4. 运行消息优先取输入框文本，否则取项目目标。
5. 有额外输入时，先把用户事件写入 `runs/events.jsonl` 并追加到当前时间线。
6. 项目状态写为 `RUNNING`，当前步骤为“Deep Agents 执行中”，进度初始值为 `0 / max_steps`。
7. 捕获发起时的 `project_id` 到 `_worker_project_id`，避免用户运行中切换项目后事件串写。
8. 创建 `AgentWorker(QThread)`，绑定事件、审批、澄清、完成、模型错误信号，然后启动线程。

### 5.2 Worker 线程

`AgentWorker.run()` 的顺序：

1. 等待或补做引擎预热。
2. 按项目模型解析链拿到 `ResolvedModel`。
3. 进入 `project_run_slot(project.id)`。同一项目按提交顺序串行；不同项目可以并行。
4. 创建 `AgentRunner` 和 `RunRequest`。
5. 注入三个回调：普通事件、审批中断、`ask_user` 澄清。
6. `mode=run` 调用 `runner.run(request)`；桌面“发送”则走 `mode=chat` 调用 `runner.run_chat(request)`。
7. 结果通过 Qt 信号回到主线程；无论 Runner 内部异常还是正常结果，Worker 都会尽量发出 `finished_result`，避免 UI 永久停在运行状态。

### 5.3 模型解析

`resolve_model_for_project()` 当前顺序：

```text
项目 project.provider + project.model_id
  → 厂商设置中的默认模型
  → WokBee 设置中的 default_provider + default_model_id
  → 厂商列表第一个可解析模型
  → 没有可用模型则失败
```

项目已有绑定但绑定失效时，会继续向后回退。AutoBee 还会先尝试任务自身配置的执行模型；网关使用同一套项目解析链。

## 6. Agent 构建阶段

入口：`src/wokbee/engine/runner.py::AgentRunner.build_agent()`。

### 6.1 创建项目运行环境

运行/交互模式会确保项目标准目录存在，并重建/刷新 `memory/AGENTS.md` 和经验索引。设计模式是 DeziBee 特例，不走 WokBee 的经验管线。

模型请求使用 `model_timeout_seconds`，项目后端默认使用 `tool_timeout_seconds`。当前默认值分别是 180 秒和 90 秒。

### 6.2 文件和路径边界

Agent 的默认项目后端是 `ArchiveDeniedBackend`：

- 文件工具使用项目虚拟路径，默认根为项目目录。
- `archives/` 被禁止读取、列举、搜索和写入。
- `execute` 可运行真实主机命令，但同样有归档路径检查。
- Windows 命令使用可取消子进程执行；暂停或超时会尝试杀掉进程树。
- `AccessCoerceBackend` 负责把已授权的真实目录转换为 `/ext/<slug>/...` 虚拟路由。
- 全局 Skills 使用只读 Backend，Agent 可以读，不能修改全局 Skill。
- `write_file` / `edit_file` / `insert_text` / `write_file_chunk` 等操作经过原子写入、锚点和路径保护。

### 6.3 工具集合

每次构建 Agent 会组合并稳定排序以下工具：

- 网络：`web_search`、`http_get`、`http_request`，可选 `deepseek_web_search`。
- 文件：Deep Agents 文件工具，以及本项目的 `find_in_file`、`read_file_range`、`insert_text`、`write_file_chunk`。
- 项目元信息：读取/修改项目名称与目标。
- 凭据：只暴露环境变量名，不把密钥写进模型回复或文件。
- 经验：运行模式挂载 `update_project_experience`。
- 人机协作：`ask_user`、`request_access`。
- AutoBee：创建/管理定时任务的工具。
- MCP：加载已启用的 MCP 工具；未勾选常规免审时，MCP 工具会进入审批列表。

工具结果会统一截断；超时工具的结果交给 AI 接管。`ask_user`、`task`、`request_access` 等特殊工具不套普通硬超时，以免吞掉中断或杀掉合法的长任务。

### 6.4 Prompt 和缓存

运行模式的 Prompt 分成两部分：

- 静态 `system_prompt`：身份、能力、硬规则、文件操作规范、pipeline 规则和经验维护规则。
- 首条用户消息前的 `【会话上下文】`：项目名称、目标、审批策略、步数提示、运行环境、最新经验、Skills 和附加目录。

上下文只注入首条用户消息；后续阶段通过 checkpointer 保持状态。工具名稳定排序，并计算 prefix fingerprint，用于保持 DeepSeek 前缀缓存的稳定性。流式 Token 不写入事件文件，完整 Agent 消息在 update 阶段再落盘，避免 `events.jsonl` 被 token 级事件刷爆。

## 7. 有序 pipeline 和 AI 执行循环

### 7.1 没有 pipeline

没有 `scripts/pipeline.json` 或步骤为空时，直接进入完整 AI 流程：

```text
原始目标/指令
  → Agent 自主判断
  → 读取/联网/执行/写文件/MCP/Skills
  → 需要时审批或澄清
  → Agent 结束
```

首次成功收尾时，系统要求 Agent 使用 `update_project_experience` 固化经验；如果 Agent 没有写，系统会在结束阶段做一次 AI 总结兜底。
固化时按真实成功路径逐步记录，不强制合并相邻的同类型步骤。例如可以固化为：

```text
脚本1 → 脚本2 → 脚本3 → AI整理 → 脚本4 → AI成文 → 脚本5 → AI验证/修复
```

其中每个脚本/AI 业务动作保留独立步骤，后续按数组顺序执行；多个连续脚本由主机连续运行，不会为每个脚本额外调用 AI。

### 7.2 已有 pipeline

`script_runner.run_pipeline_until_ai_or_end()` 读取并规范化 `steps`，每个数组元素保留为独立步骤，例如：

```text
script → ai → script → ai
```

实际过程：

1. 每个 `script` 步骤按数组顺序执行；相邻脚本不合并为一个逻辑步骤，但主机不会因此增加 AI 调用。
2. 每个脚本必须位于项目 `scripts/` 下，按扩展名选择 Python、cmd、PowerShell、Node、bash 或 cscript。
3. 脚本失败、超时、取消、触碰 `archives/` 或输出失败哨兵时停止当前步骤，交给 AI 处理当前异常。
4. 脚本 stdout/stderr 写入 `workspace/script_callback_<脚本名>.md`，作为后续 AI 的事实输入。
5. 遇到 `ai` 步骤时，Runner 构造“只执行当前固定业务任务”的提示词；禁止重新规划整个 Pipeline 或越权执行后续步骤，但允许重复之前的脚本做验证。
6. 每个步骤结束都会写状态事件：`success`、`失败-AI接管后成功` 或 `失败-AI接管后失败`。
7. AI 步骤结束后，Runner 从 checkpointer 取出文本，追加到上下文，继续执行 pipeline 的后续步骤。
8. 如果脚本失败，进入“异常接管”提示词；AI 只处理当前异常，并可通过 `update_project_experience` 提交当前失败步骤的修正版。
9. 如果整条管线只有脚本且全部成功，直接返回成功、不调用 LLM；保留脚本生成的原始 txt/json 等格式，不再强制生成 `script_result.md`。目标要求交付物时，实际产物复制到 `deliverables/`。
10. 步骤循环有 `max_pipeline_phases` 上限，默认 64；Agent 自身另受 `max_steps` 硬上限约束。

### 7.3 一轮 AI turn 的内部过程

每个 AI 阶段都会：

1. 调用 `agent.stream(..., stream_mode=["messages", "updates"])`。
2. 旁路线程接收流，按约 80ms 批量把 reasoning/text 增量发给 UI。
3. 收到完整消息 update 后，识别 Agent 消息、工具调用、工具回调，写入本轮内存轨迹并回调 UI。
4. 若出现 Graph interrupt，转到审批/澄清处理；否则继续到本 turn 结束。
5. 每轮结束检查消息历史是否破坏 append-only 前缀；若漂移，记录缓存失效错误。
6. Runner 为每个 Agent turn 和工具更新计数；同时把图级 `recursion_limit` 写入 graph config。超过 `max_steps` 时返回 `outcome="incomplete"`，并带明确的上限与已用步数。
7. 工具包装层共享 `max_parallel_tools` 信号量；同步/异步工具均受同一执行链上的并发上限控制。

## 8. 审批、澄清、暂停和错误

### 8.1 审批

`ApprovalFlags` 按风险桶决定工具调用前是否 interrupt：

| 风险桶 | 工具示例 | 默认 |
|---|---|---|
| 读 | `ls`、`read_file`、`glob`、`grep` | 免审 |
| 写 | `write_file`、`edit_file`、`delete` | 需审 |
| 常规/联网 | `web_search`、`http_get`、`http_request`、`task` | 需审 |
| 高危 | `execute`、`request_access`、`get_credential` | 需审 |

Agent 进入 interrupt 后，Runner 在后台线程等待 UI 通过 `approve_all()` 或 `reject_all()` 回传决策，默认审批等待上限约 1 小时。用户暂停时会同时设置取消事件、尝试终止本次可取消命令，并唤醒审批/澄清等待。

### 8.2 `ask_user`

`ask_user` 是工具内部的 interrupt，不重复套工具审批。桌面端弹窗收集答案；网关端优先把问题发到手机并等待回复；AutoBee 或没有可用交互通道时自动选择每题第一项，避免无人值守任务永久阻塞。

### 8.3 错误和超时

- 模型错误统一分类；网关 500、鉴权失败、额度耗尽、上下文过长等会给出不同提示。
- 单次模型请求默认 180 秒；流式请求按相邻数据间隔判断，持续输出不会被误判为超时。
- 工具默认 90 秒，Agent 可以用 `timeout_seconds` 覆盖。
- `execute` 和固化脚本使用墙钟轮询、取消事件和进程树终止，规避 Windows 父进程退出但孙进程仍持有管道的卡死问题。
- UI 结束回调将状态设为 `DONE`、`IDLE`、`FAILED` 或 `AWAITING_APPROVAL`，并清理当前 Worker 引用。
- pipeline 每个步骤结束另外写入 `pipeline_status`：`success`、`失败-AI接管后成功` 或 `失败-AI接管后失败`。

## 9. 运行收尾、经验和脚本固化

### 9.1 结束条件

桌面 Agent 运行结果最终由 `_on_engine_finished()` 映射为：

| Runner outcome | 项目状态 | 说明 |
|---|---|---|
| `success` | `DONE` | 追加交付物目录入口；刷新产物摘要 |
| `cancelled` | `IDLE` | 记录已取消，不写取消类经验 |
| `awaiting_approval` | `AWAITING_APPROVAL` | 仍有待审批操作 |
| `incomplete` | `IDLE` | 记录未完成 |
| `failed` | `FAILED` | 追加错误事件 |

### 9.2 经验写入策略

当前不是“每轮都总结”，而是尽量省一次模型调用：

1. Agent 在运行中主动调用 `update_project_experience`：直接写经验、脚本和 pipeline，并标记本轮已更新。
2. 首次运行、Agent 没有写经验：结束时 AI 总结兜底。
3. 非首次运行但本轮出现脚本/工具错误：结束时 AI 总结复核每个步骤状态并修正；即使 Agent 已经更新经验，也会再经过这个复核。
4. 非首次且执行干净：不自动总结。
5. 取消：不更新经验。

AI 总结经验和 pipeline 的输入包括：项目目标、上一份经验、最新一轮运行日志、脚本/pipeline 上下文、本机环境摘要，以及本轮逐步状态。总结结果会：

- 新建 `memory/experiences/exp_*.md`，旧经验不覆盖；运行时只注入最新一份。
- 根据真实工具轨迹识别可复用步骤，生成或保留 `scripts/` 脚本。
- 按 AI 给出的真实步骤顺序合并 `script_files` 和 `pipeline_steps`，写入 `scripts/pipeline.json`，不再把未覆盖脚本统一前插。
- 保存使用过的 Skill 快照和参考材料清单到 `uploads/references/`。
- 不清理已有 `scripts/`；普通归档也保留 `scripts/`、经验和 `uploads/`。

## 10. 其他两个运行入口

### 10.1 AutoBee 定时运行 WokBee

```text
APScheduler
  → SchedulerService._run_job()
  → TaskExecutor.run()
  → TaskExecutor._run_wokbee()
  → project_run_slot + 本地 project lock
  → AgentRunner.run()
  → 全自动审批 / 自动回答 ask_user
  → 写项目事件和 AutoBee 运行日志
  → 更新项目状态
  → 成功时写入 `deliverables` 事件
  → 已打开的 WokBee 时间线即时显示「交付物目录」气泡；未打开项目则在下次打开时从事件记录恢复
  → 可选企业微信 / 微信通知
```

AutoBee 启动时从 `autobee.json` 恢复任务并注册 Cron；默认调度器线程池最多同时跑 2 个任务，单个任务 `max_instances=1`。WokBee 定时执行当前不自动归档，会直接复用当前项目会话和经验/脚本。

### 10.2 微信 / 飞书消息网关

```text
频道长连接
  → GatewayManager._on_incoming()
  → 消息 ID TTL 去重
  → inbox.Queue
  → gw-dispatch 线程
  → ThreadPoolExecutor（最多 2 个处理线程）
  → 白名单与 #/@ 路由
  → project_run_slot + GatewayManager project lock
  → GatewayDispatcher.run_chat()
  → 全自动审批；ask_user 优先发回手机等待答复
  → 回写 runs/events.jsonl
  → 回复手机 + 通知桌面时间线
```

路由规则：

- `#new` 新建项目并绑定当前频道默认项目。
- `#list` 列项目。
- `#run` 使用当前频道默认项目目标运行。
- `@项目ID` 切换频道默认项目；带正文时切换后立即运行正文。
- 普通消息进入当前频道默认项目。
- 白名单为空或发送者不在白名单时直接拒绝。

网关使用 `run_chat()`，所以它是“完整能力交互模式”，不自动跑 WokBee 有序 pipeline；但会读写项目时间线并复用 `wokbee-chat-<project_id>` 的进程内 checkpointer。

## 11. 并发和生命周期

### 11.1 当前并发模型

| 场景 | 行为 |
|---|---|
| 不同项目 | 可以同时运行；每个项目有独立工作区、UI 工作区和运行上下文 |
| 同一项目多次触发 | 由 `project_run_slot()` 按 ticket FIFO 串行 |
| 同一项目桌面切换 | 只隐藏 UI，后台 Worker 继续运行 |
| 网关多个频道 | 飞书/微信连接可同时在线；各自消息进入共享处理池 |
| AutoBee 多任务 | 调度器最多 2 个任务并行，项目运行槽再做项目级串行 |
| 应用退出 | 停止 Scheduler/Gateway，取消并等待 WokBee 的 Agent、经验、压缩、改名 Worker |

### 11.2 当前状态的持久化边界

- 项目元数据和事件落盘，应用重启后可以恢复项目列表和时间线。
- `AgentRunner` 的 LangGraph checkpointer 是进程内 `InMemorySaver`；应用重启后不会恢复 Agent 图状态。
- WokBee 交互模式的近期上下文通过 `runs/events.jsonl` 和 `memory/context_state.json` 摘要辅助恢复，但这不等同于恢复完整 Agent checkpoint。
- `scripts/`、最新经验和 `uploads/` 是跨运行复用的主要载体。

## 12. 优化候选清单

以下按优先级排序。P0/P1 是当前代码已经能确认的实现问题或明显不一致；本轮已落实的 P0 记录为“已修正”，剩余项目供后续继续优化。

### P0：先修正“配置名义存在、运行未真正使用”的限制

#### P0-1 `max_steps`：已修正为硬上限

Runner 现在对每次 Agent turn 和工具更新做逻辑计数，并在 graph config 传入与其匹配的 `recursion_limit`。达到上限时停止继续调用，返回 `outcome="incomplete"`，同时写出 `max_steps`、已用步数和停止原因；经验兜底会把这次未完成信息交给总结阶段。

关联位置：`src/wokbee/core/settings.py`、`src/wokbee/engine/runner.py`、`src/wokbee/ui/workspace.py`。

#### P0-2 `max_parallel_tools`：已接入 WokBee 执行链

设置页的值现在由 Runner 传给工具包装层；同一轮 Agent 的同步/异步工具共享信号量，因此 Deep Agents 同时发出多个工具调用时，实际工具体不会超过该上限。新增测试覆盖并发峰值。

### P1：减少三套入口的重复和状态分叉

桌面 `AgentWorker`、`GatewayDispatcher`、`TaskExecutor._run_wokbee` 都重复完成：解析模型、创建 `AgentRunner`、构造 `RunRequest`、挂回调、审批策略、状态/事件适配。现在功能修复需要同步检查至少三处，已经出现“桌面等待人工、AutoBee 自动放行、网关手机澄清”的分叉。

建议抽出一个最小的 `WokBeeRunService`，只统一“构造并执行一次 Runner”的部分；入口只提供：

```text
运行来源 + 审批策略 + ask_user 策略 + 事件 sink + 状态 sink
```

不要把 UI、手机回复或 AutoBee 通知塞进 Runner。这样可以集中处理模型超时、错误分类、事件去重和最终状态。

关联位置：`src/wokbee/engine/worker.py`、`src/wokbee/gateway/dispatcher.py`、`src/autobee/engine/executor.py`。

### P1：修复 Worker 启动早期的异常兜底边界

`AgentWorker.run()` 在引擎预热和 `from wokbee.engine.runner import ...` 之前，异常保护还没有完全覆盖。如果极早阶段导入失败，Qt Worker 可能直接退出而没有发出 `finished_result`，UI 有停在运行态的风险。

建议：把“预热、导入、模型解析、执行”统一放进一个最外层 `try/finally`；任何异常都发出一次明确的 `RunResult(failed)`，并保证不会重复发完成信号。

同时在 `AgentWorker.__init__` 明确初始化 `self.runner = None`。当前取消逻辑依赖该属性，快速点击暂停时存在属性尚未建立的边界。

### P1：给网关 inbox 和处理池增加背压

网关当前使用无界 `queue.Queue()`，再把消息不断提交到最多 2 个线程的池中；当 Agent 调用模型很慢时，消息会积压，用户看不到排队长度，也没有按会话限流策略。

建议按需求选择最小方案：

- 队列设置上限，满时直接回复“当前繁忙，请稍后”。
- 每个频道/会话只保留一条待处理消息，后续合并或拒绝。
- 把“排队中”写入事件，桌面和手机都能看见。

### P1：明确无人值守入口的安全策略

AutoBee 和网关目前都构造全免审的 `ApprovalFlags`。这符合无人值守设计，但意味着 `execute`、网络、写文件、凭据读取均不会等人工确认；网关只靠允许列表保护。

建议至少补充：

- AutoBee / 网关专属审批策略，而不是无条件全放行。
- 高危工具的可选禁用开关。
- 每次无人值守运行在项目事件中记录“自动审批原因和来源”。
- 网关按发送者、频道、项目分别限流，而不只做发送者白名单。

### P2：减少 Agent 构建时的工具和上下文成本

每次运行都会加载 Skills、MCP、网络工具、文件工具、项目工具、经验工具、AutoBee 工具，并进行包装、排序和 prefix fingerprint。功能完整，但对只需简单文件操作的任务来说工具面偏大，可能增加模型输入、MCP 连接等待和工具选择歧义。

建议先记录每次运行的：工具数量、MCP 加载耗时、Prompt 字符数、首次模型耗时，再考虑：

- 按运行来源/项目配置关闭不需要的工具组。
- MCP 按需加载或按项目挂载。
- AutoBee 工具只在用户明确要求定时任务时启用。
- 经验摘要从固定 3500 字改为按 pipeline/当前阶段裁剪。

### P2：统一 WokBee 的上下文压缩语义

UI 有“上下文用量”和手动压缩，压缩结果写入 `memory/context_state.json`；但 Agent 的 LangGraph checkpoint 仍是进程内 `InMemorySaver`，压缩点主要用于时间线摘要和交互提示，不会直接裁剪 checkpoint 中的完整消息。

建议明确产品语义：

- 只把压缩作为 UI/事件摘要：文案不要暗示已压缩 Agent 内部状态。
- 或在新一轮 chat 前，根据压缩点重建 Agent 输入，真正减少 checkpoint 上下文。
- 运行模式本身已经依赖经验和 pipeline，通常不需要把所有历史事件再次喂给模型，应继续保持这一边界。

### P2：清理进程级长生命周期对象

`project_run_queue` 的 gate 字典、Runner 的 checkpointer/agent 字典、网关和 AutoBee 的 project lock 字典都按项目 ID 增长并保持到进程结束。当前有意用进程级对象保证并发安全，但大量临时项目会留下无用对象。

建议只在确认项目 ID churn 是实际问题后增加 idle cleanup；否则保持现状，避免为了理论内存问题引入锁清理竞态。

### P2：补一条真正覆盖“整条运行链”的最小测试

当前测试重点在文件安全、路径转换和 DeziBee Worker 生命周期；建议增加一个不调用真实模型的 Fake Runner/Backend 链路，覆盖：

```text
无 pipeline → failed/success 状态
有 script → callback → ai → 后续 script
脚本超时/取消 → AI 接管
审批 interrupt → resume
首次成功 → lesson + pipeline
同一 project 的桌面/AutoBee/网关入口不并发
```

不需要引入大型测试框架；用现有 `unittest`、临时目录和 fake model 就足以抓住当前最容易分叉的逻辑。

## 13. 建议的优化顺序

```text
第一步：补 Worker 启动异常和快速取消边界
  ↓
第二步：抽取最小 RunService，收敛桌面 / AutoBee / 网关的 Runner 适配
  ↓
第三步：给网关加背压和无人值守安全策略
  ↓
第四步：根据工具数、MCP 耗时、Prompt 大小、模型耗时数据做按需加载
  ↓
第五步：再决定是否改 Agent checkpoint / 上下文压缩和进程级对象清理
```

## 14. 代码定位索引

| 主题 | 位置 |
|---|---|
| 应用启动与引擎预热 | `main.py`、`src/tokbee/app.py`、`src/wokbee/engine/__init__.py` |
| WokBee 页面和项目切换 | `src/wokbee/ui/wokbee_view.py` |
| 桌面运行/发送/暂停/状态 | `src/wokbee/ui/workspace.py` |
| 后台 Agent/经验线程 | `src/wokbee/engine/worker.py` |
| Agent 构建、流式、审批、运行循环 | `src/wokbee/engine/runner.py` |
| pipeline 读取与执行 | `src/wokbee/engine/script_runner.py` |
| 经验、脚本、pipeline 固化 | `src/wokbee/engine/lessons.py`、`src/wokbee/engine/script_factory.py` |
| 项目目录、事件、归档 | `src/wokbee/core/paths.py`、`src/wokbee/core/project_store.py` |
| 同项目 FIFO 运行槽 | `src/wokbee/core/project_run_queue.py` |
| 审批分类 | `src/wokbee/engine/approval_policy.py` |
| 文件/归档/外部目录安全 | `src/wokbee/engine/archive_guard.py`、`access_coerce.py`、`access_request.py` |
| AutoBee 入口 | `src/autobee/engine/scheduler.py`、`src/autobee/engine/executor.py` |
| 微信/飞书入口 | `src/wokbee/gateway/manager.py`、`router.py`、`dispatcher.py` |
