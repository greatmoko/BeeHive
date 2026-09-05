# AI API 调用错误码速查与处置对照表

本表汇集主流 OpenAI 兼容 API（OpenAI / DeepSeek / 各类中转网关等）常见的调用错误，
给出归一化分类、自动处置策略与人工建议。分类与处置遵循开源社区（openai SDK、
langchain-openai、LiteLLM）的通行约定，并已接入 WokBee 的 Agent 处理管线
（`wokbee/engine/ai_errors.py`）。

## 分类概览

| 归一化分类 (AIErrorKind) | 含义 | 可自动重试 |
| --- | --- | --- |
| `RATE_LIMIT` | 429 限速 | ✅ 退避重试 |
| `RATE_QUOTA` | 额度/余额耗尽 | ❌ |
| `TRANSIENT` | 网络/超时/连接临时故障 | ✅ 退避重试 |
| `SERVER` | HTTP 5xx / 上游故障 | ✅ 短暂重试 |
| `CONTEXT_LENGTH` | 上下文超长 | 压缩上下文后重试 |
| `AUTH` | 401/403/Key 无效 | ❌ |
| `INVALID_REQUEST` | 参数/模型名/格式错误 | ❌ |
| `MODEL_NOT_FOUND` | 404 / 模型不存在 | ❌ |
| `CONTENT_FILTER` | 内容被审核拦截 | ❌ |
| `UNSUPPORTED` | 模型/端点不支持该操作 | ❌（可改参数） |
| `UNKNOWN` | 无法识别 | ❌ |

## 常见错误码/错误类型 → 分类 → 处置

### HTTP 状态码

| 状态码 | 常见场景 | 自动处置 | 人工建议 |
| --- | --- | --- | --- |
| **400** | 请求参数非法、字段不兼容、消息格式错误 | 不重试 | 核对请求参数与模型能力 |
| **401** | `invalid_api_key`：API Key 错误或未填 | 不重试 | 「厂商设置」检查 Key |
| **402** | `insufficient_quota`：余额/额度耗尽 | 不重试 | 控制台充值/扩容 |
| **403** | `permission_denied` / 无权访问该模型 | 不重试 | 检查权限/Key 归属 |
| **404** | `model_not_found`：模型 ID 不存在/未启用 | 不重试 | 「厂商设置」启用或改模型 ID |
| **422** | 请求无法处理（参数字段不兼容） | 不重试 | 调整请求/进参 |
| **429** | `rate_limit`：请求过频被限速 | **退避重试**（尊重 `Retry-After`） | 降低并发/节流间隔，或提额 |
| **500/502/503/504** | 服务端/上游/网关故障 | **短暂退避后重试** | 厂家侧问题，稍后重试/查状态页 |

### 常见错误 type / code 特征（供应商返回体）

| 特征串（出现即命中） | 分类 | 处置 |
| --- | --- | --- |
| `rate_limit` / `requests_rate_limited` | RATE_LIMIT | 退避重试 |
| `insufficient_quota` / `quota exhausted` / `out of credits` / `no balance` / `billing` | RATE_QUOTA | 充值/扩容，不重试 |
| `authentication_error` / `invalid_api_key` / `forbidden` / `access denied` / `permission_denied` | AUTH | 检查 Key，不重试 |
| `model_not_found` / `model does not exist` / `unknown model` | MODEL_NOT_FOUND | 启用/改模型，不重试 |
| `context_length_exceeded` / `maximum context length` / `context window` / `request_too_large` / `too many tokens` | CONTEXT_LENGTH | 压缩上下文后重试 |
| `content_filter` / `prompt was blocked` / `moderation` / `safety` | CONTENT_FILTER | 调整内容，不重试 |
| `invalid_request_error` / `invalid parameter` / `bad_request` / `param_error` | INVALID_REQUEST | 核对参数，不重试 |
| `not_supported` / `does not support` / `unsupported` | UNSUPPORTED | 改参数/降级 |

## 处置策略矩阵（管线内实际执行）

- **自动重试**：`RATE_LIMIT` / `TRANSIENT` / `SERVER` 走退避重试（尊重服务端
  `Retry-After`，有次数与超时上限，并受取消信号控制）。主模型路径由
  `ChatOpenAI(max_retries=1)` / deepagents 承担；自研客户端
  (`tokbee.core.ai_client`) 内置指数退避重试。
- **上下文超长**：`CONTEXT_LENGTH` 命中分类后，理想做法是压缩上下文再重发。
  WokBee 主线因含审批态与有序管线阶段，不自动重放整轮（避免重复副作用），
  而是给出中文指示（缩短输入 / 减少历史 / 换更大窗口模型）。对话压缩所使用的
  接口见 `tokbee.core.context_manager`。
- **鉴权/额度**：`AUTH` / `RATE_QUOTA` / `MODEL_NOT_FOUND` / `CONTENT_FILTER` /
  `INVALID_REQUEST` / `UNSUPPORTED` 均不自动重试，给出定位与修正建议。
- **副线/后台总结**（对话记忆、经验总结、Agent 记忆、DeepSeek 搜索）在临时故障时
  静默降级（不阻断主流程），鉴权/额度类会带上分类信息便于排查。

## 错误入口（代码位置）

| 位置 | 作用 |
| --- | --- |
| `wokbee/engine/ai_errors.py` | 统一分类器 `classify_error` / `http_status_to_kind` / `display_message` |
| `tokbee/core/errors.py` | `AIError` 携带 `kind`/`status_code`/`code`，惰性 `classify()` |
| `tokbee/core/ai_client.py` | 自研客户端 HTTP 错误强制分类，指数退避重试 |
| `wokbee/engine/runner.py` `_format_engine_error` | 主线 deepagents 错误统一分类诊断 |
| `wokbee/engine/deepseek_search.py` | DeepSeek 服务端搜索错误按状态码分类 |
