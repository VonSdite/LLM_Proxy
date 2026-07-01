# 4+1 Architecture

## 1. Context

这个项目是一个协议翻译型 LLM Proxy。

它的目标不是“支持所有厂商的所有接口”，而是用一套干净的首版架构，稳定支持：

- 上游协议族
  - `openai_chat`
  - `openai_responses`
  - `claude_chat`
- 下游协议面
  - `POST /v1/chat/completions`
  - `POST /v1/responses`
  - `POST /v1/messages`
  - `GET /v1/models`
  - `OPTIONS /v1/*` CORS 预检

这个版本不保留 Gemini / Antigravity。配置加载阶段会清理少量历史废弃字段并回写配置文件；除此之外不再继续扩展旧字段兼容逻辑。

控制平面除了 Provider、Auth Group、用户、统计和系统设置，还可以：

- 在启用 `oauth.enabled` 后提供 OAuth 管理入口，用于生成、查看、导入、导出和归档删除 CLI/OAuth 类本地认证文件
- 在启用 `api_keys.enabled` 后提供 API Key 管理入口，用于创建下游访问 key、设置模型权限、设置总 token 上限并查看 key 级用量

## 2. Logical View

### 2.1 Core Pipeline

系统按下面的统一链路工作：

```text
downstream request
  -> data-plane CORS preflight / response headers
  -> controller
  -> provider lookup
  -> resolve route model key to upstream model id
  -> header_hook
  -> translator.translate_request()
  -> request_guard
  -> usage request enrichment / provider protocol signing
  -> executor
  -> decoder
  -> translator.translate_response()
  -> response_guard
  -> encoder
  -> downstream response
```

补充：hook 在 retry 场景下还可以读取上一轮失败摘要，用于做轻量级重试决策：

- `last_status_code`
- `last_error_type`

模型语义：

- 数据平面下游请求中的 `model` 是代理路由 key，格式为 `{provider_name}/{upstream_model_id}`
- 进入上游请求构建前会先解析出真实 `upstream_model_id`
- `request_guard` 接收的是即将发往上游的请求体，`body["model"]` 为真实上游模型 ID
- `HookContext.request_model` 保留下游路由 key，`HookContext.upstream_model` 保留当前真实上游模型 ID

### 2.2 Major Components

- `ProxyController`
  - 根据当前 route family 选择下游接口协议
  - 根据 `client_ip.real_ip_enabled` 和 `client_ip.real_ip_header` 解析白名单、模型权限、统计和 trace 使用的客户端 IP
  - 在 `api_keys.enabled=true` 时校验下游 API Key，并按 key 模型权限与总 token 上限收窄可访问模型和请求
  - 同时启用 Chat 白名单和 API Key 管理时，最终模型权限为用户权限与 key 权限的交集
  - 构造标准错误体
- `DataPlaneCors`
  - 只为 `/v1/*` 数据平面添加 CORS 响应头
  - 直接处理 `OPTIONS /v1/*` 预检请求
- `WebController`
  - 提供 Provider、用户、API Key、统计与系统设置页面
  - 暴露统计汇总、用户用量汇总、请求明细、当前页签 Excel 导出与日聚合统计 JSON 导入导出接口
  - 在 `oauth.enabled=true` 时显示 OAuth 顶层导航入口
  - 在 `api_keys.enabled=true` 时显示 API Key 管理顶层导航入口
  - 暴露系统设置读取与保存接口
- `ProviderService`
  - 维护 Provider 配置的创建、复制、编辑、删除、启停、排序和批量删除
  - 按选中 Provider 导出 JSON，导入 Provider JSON 时为同名配置生成数字后缀名称
- `UserService`
  - 维护用户、IP 白名单状态和模型权限
  - 支持用户 JSON 导入导出与批量删除；导入时重复 IP 作为已有用户跳过
- `LogService`
  - 读取请求日志、统计汇总、用户用量汇总和筛选项
  - 支持日聚合统计 JSON 导入导出；导入时按日期、IP、请求模型和响应模型累加合并
- `ApiKeyController`
  - 暴露 API Key 列表、创建、编辑、启停、删除、模型权限和 token 上限更新接口
- `ApiKeyService`
  - 生成 `sk-` 前缀的下游 API Key
  - 持久化明文 key 供管理端复制和显示，同时保留 hash 用于数据面鉴权
  - 复用 `*` / 显式模型列表语义维护 key 模型权限
  - 校验数据面请求携带的 `Authorization: Bearer sk-...` 或 `X-API-Key`
- `ModelCatalogService`
  - 汇总模型权限控制平面的可选模型目录
  - Provider 模型从配置中的 `providers[].model_list` 读取，继续包含已禁用 Provider 的模型
  - OAuth 模型从 Codex / Claude OAuth 服务读取当前可用模型目录
  - 供用户模型权限与 API Key 模型权限的选择、保存校验、展示计数和同步清理共用
- `OAuthController`
  - 暴露 Codex / Claude OAuth 登录、回调提交、认证文件列表、JSON/ZIP 导入、ZIP 导出、归档删除与 Codex 配额刷新接口
- `CodexOAuthService`
  - 生成 Codex OAuth PKCE 授权链接
  - 使用回调 URL 换取 token 并写入本地认证文件
  - 导入认证文件时支持多 JSON 和多 ZIP，逐个校验合法内容后写入
  - 导出选中认证文件时始终打包为 ZIP
  - 删除本地认证文件时移动到 `data/oauth/codex/deleted/`，并同步清理该文件的本地状态
  - 读取认证文件状态，并按需刷新 token 后查询 Codex 配额
  - token 交换、token 刷新与配额查询遇到代理风险确认页时，会走统一自动确认重试流程
  - 按认证文件名限制同一时刻只有一个配额刷新请求会真实访问上游
  - 持久化认证文件最近一次配额快照、配额刷新错误、最近成功认证文件与 Codex 模型代理使用状态
  - 维护本地手动 Codex OAuth 模型目录
  - 按本地模型目录、本地冷却、认证失败状态和最近成功认证文件提供 Codex 请求候选账号
- `ClaudeOAuthService`
  - 生成 Claude OAuth PKCE 授权链接
  - 使用回调 URL 或手动粘贴的 `code#state` 换取 token 并写入本地认证文件
  - 列出、JSON/ZIP 导入、ZIP 导出和归档删除 `data/oauth/claude/*.json`
  - 维护本地手动 Claude OAuth 模型目录
  - 按本地模型目录、认证失败状态和最近成功认证文件提供 Claude 请求候选账号
  - token 交换与 token 刷新遇到代理风险确认页时，会走统一自动确认重试流程
- `CodexProxyService`
  - 代理下游直接使用的 Codex 普通模型名
  - 使用 `data/oauth/codex/*.json` 中的 OAuth access token 请求 Codex backend
  - 遇到账号配额耗尽时标记临时冷却并尝试下一个账号
  - 将每个认证文件最近一次数据面成功或失败结果写回 OAuth 状态
- `ClaudeProxyService`
  - 代理下游直接使用的 Claude 普通模型名
  - 使用 `data/oauth/claude/*.json` 中的 OAuth access token 请求 Anthropic Messages
  - 按 CPA / CLIProxyAPI 请求方式补齐 Claude Code OAuth headers，并在存在 billing header 时重签 `cch`
  - 将每个认证文件最近一次数据面成功或失败结果写回 OAuth 状态
- `ProviderManager`
  - 加载 provider 配置
  - 维护 `provider/model -> provider` 映射
- `AuthGroupManager`
  - 加载 `auth_groups`
  - 选择 `auth_entry`
  - 支持 `least_inflight` 与 `sticky_failover` 两种 entry 选择策略
  - 维护进程内 inflight 计数和组级选择游标
  - 该游标在 `least_inflight` 中用于同负载轮转，在 `sticky_failover` 中表示当前粘滞 entry
  - 持久化冷却、禁用与配额运行态
- `ProxyService`
  - 组装整条代理链路
  - 根据当前请求所处接口选择 translator 和 encoder
  - 对 `source_format=claude_chat` 的 Provider，在上游 body 已有 Claude Code billing header 时重签 `cch`
  - 在开启 `logging.llm_request_debug_enabled` 时输出独立 trace
- `ProviderModelTestService`
  - 复用 translator / executor / request-side hook
  - 按当前 Provider 表单快照直连上游测试模型可用性、首字延迟与 TPS
  - 在协议支持时显式请求 usage 返回
  - 批量测试按前端当前选择的模型行逐条执行并逐条回填结果
- `SettingsService`
  - 维护 `server`、`admin`、`oauth`、`api_keys` 与 `logging`
  - 管理立即生效项与重启生效项的边界
- `ProviderRuntimeFactory`
  - 负责临时 / 正式 Provider 运行时对象构建
  - 统一 hook 加载与缓存
- `ExecutorRegistry`
  - 负责 HTTP 上游连接
  - 统一处理 Provider 出站请求中的代理风险确认页自动确认与一次重试
- `Decoder`
  - 将上游流拆成统一事件
- `TranslatorRegistry`
  - 负责协议适配
  - 维护 OpenAI Chat `reasoning_effort`、OpenAI Responses `reasoning.effort` 与 Claude `thinking` 的请求语义映射
  - 将 OpenAI Chat 上游 reasoning 内容转换为下游协议对应的 thinking / reasoning 输出
- `Encoder`
  - 将统一 chunk 编码成下游协议
- `Hook`
  - 只负责 header 和 guard
  - `request_guard` 运行在协议转换之后，用于上游前的厂商私有参数适配
  - 内置上游思考参数 Hook 位于 `hooks/openai_reasoning_compat.py`
  - MiniMax、DeepSeek、GLM / Z.AI、Qwen / DashScope 各有独立处理类和单厂商入口文件

Hook 组件除了 header / guard，还会收到最小重试上下文：

- `retry`
- `auth_group_name`
- `auth_entry_id`
- `last_status_code`
- `last_error_type`

### 2.3 Protocol Families

| family | 用途 |
| --- | --- |
| `openai_chat` | OpenAI Chat Completions 语义 |
| `openai_responses` | OpenAI Responses 语义 |
| `claude_chat` | Anthropic Messages 语义 |

OpenAI Chat SSE 下游编码会移除空的 `choices[].delta.tool_calls`，避免兼容客户端把空列表误判为工具调用开始；非空工具调用保持原样。

## 3. Process View

### 3.1 Downstream Route Contract

route family 直接决定当前请求的下游接口协议：

| route | downstream protocol |
| --- | --- |
| `/v1/chat/completions` | `openai_chat` |
| `/v1/responses` | `openai_responses` |
| `/v1/messages` | `claude_chat` |

`GET /v1/models` 除了模型 id，还会返回模型所属 provider 的：

- `source_format`

当 `api_keys.enabled=true` 时，`GET /v1/models` 与模型请求接口一样必须携带有效 API Key。返回模型列表会按以下顺序收窄：

1. 当前运行时可用模型
2. 如果启用 Chat 白名单，取当前客户端 IP 对应用户可访问模型
3. 如果启用 API Key 管理，再取当前 key 可访问模型

因此同时启用用户模型权限和 API Key 模型权限时，下游最终看到和能请求的模型是两者交集。

OAuth 模型是数据平面的例外路由：

- Provider 配置模型仍使用 `{provider}/{model}` key
- Codex / Claude OAuth 模型使用原始模型名，例如 `gpt-5-codex`、`claude-sonnet-4-5`
- 用户模型权限和 API Key 模型权限的可选目录同时包含 Provider 模型和当前可用 OAuth 模型
- 权限字段保存显式列表时，Provider 模型保存 `{provider}/{model}`，OAuth 模型保存原始模型名
- `ProxyController` 先查 Provider 映射，未命中时再查 Codex OAuth 模型目录，最后查 Claude OAuth 模型目录
- `/v1/models` 对 Codex OAuth 暴露普通模型名，`provider_name` 固定为 `codex`
- `/v1/models` 对 Claude OAuth 暴露普通模型名，`provider_name` 固定为 `claude`
- Codex OAuth 代理复用 translator registry，把下游 `openai_chat` / `openai_responses` / `claude_chat` 转成 Codex backend 的 Responses 请求
- Claude OAuth 代理复用 translator registry，把下游 `openai_chat` / `openai_responses` / `claude_chat` 转成 Anthropic Messages 请求

`OPTIONS /v1/*` 由表现层 CORS 钩子直接返回 `204`，用于支持浏览器、Obsidian 插件等第三方应用的跨域预检，不进入 provider lookup、白名单校验或上游代理链路。实际 `/v1/*` 响应也会附加 CORS 响应头；后台 `/api/*` 和管理页面不开放跨域。

### 3.2 Control-Plane Settings Contract

系统设置页与配置接口：

- 页面
  - `GET /settings`
- API
  - `GET /api/settings/system`
  - `PUT /api/settings/system/basic`
  - `PUT /api/settings/system/client-ip`
  - `PUT /api/settings/system/oauth`
  - `PUT /api/settings/system/api-keys`
  - `PUT /api/settings/system/debug`
  - `PUT /api/settings/system`

当前支持的配置项：

- `server.host`
- `server.port`
- `admin.username`
- `admin.password`
- `client_ip.real_ip_enabled`
- `client_ip.real_ip_header`
- `logging.path`
- `logging.level`
- `logging.llm_request_debug_enabled`
- `oauth.enabled`
- `oauth.proxy_mode`
- `oauth.proxy`
- `oauth.verify_ssl`
- `api_keys.enabled`

行为约束：

- `server.*` / `admin.*`
  - 归类为“基础设置”
  - 需要显式点击保存后提交
- `server.host` / `server.port`
  - 保存时写回配置文件
  - 如果值发生变化，需要重启服务后生效
- `admin.username` / `admin.password`
  - 两者都非空时启用后台登录
  - 任一为空时关闭后台登录
  - 保存后会清空进程内 session，避免旧凭据继续生效
- `client_ip.real_ip_enabled`
  - 归类为“客户端 IP”
  - 页面修改后自动生效
  - 保存后立即影响数据平面白名单、模型权限、访问日志、请求统计和 trace 中使用的客户端 IP
  - 默认值为 `false`，关闭时使用连接到本服务的对端 IP
- `client_ip.real_ip_header`
  - 归类为“客户端 IP”
  - 页面修改后自动生效
  - 仅在 `client_ip.real_ip_enabled=true` 时参与客户端 IP 解析
  - 默认值为 `X-Forwarded-For`
  - header 值为逗号分隔列表时取第一个 IP
  - header 缺失或首个值不是合法 IP 时回退到对端 IP
  - 仅应在可信反向代理会覆盖该 header 的部署中开启
- `logging.*`
  - 归类为“Debug”
  - 页面修改后自动生效
- `logging.path` / `logging.level`
  - 保存后会重新装配 logger
  - 新请求会按新的日志路径和日志级别输出
- `logging.llm_request_debug_enabled`
  - 打开后写入独立 trace 日志
  - 记录四个阶段：
    - 下游请求
    - 转换后的上游请求
    - 上游响应
    - 转换后的下游响应
  - 每条记录包含起始行、header 与 payload
- `oauth.enabled`
  - 保存后立即影响管理后台顶部 OAuth 页签是否显示
  - 默认值为 `false`
  - 只有开启后，系统设置页才展示 OAuth 出站代理和 SSL 校验设置
- `oauth.proxy_mode`
  - 保存后立即影响 OAuth 控制平面请求和 OAuth 数据面代理
  - 支持 `direct` / `system` / `custom`
  - `direct` 会绕开进程环境代理，`system` 会使用进程环境代理，`custom` 会读取 `oauth.proxy`
- `oauth.proxy`
  - 保存后立即影响 OAuth 控制平面请求
  - 用于 Codex / Claude OAuth token 交换、token 刷新、Codex 配额查询与 OAuth 数据面代理
  - 仅在 `oauth.proxy_mode=custom` 且非空时生效，`custom` 空值按直连执行
  - 自定义代理 URL 中 userinfo 的账号密码会在保存时规范化转义
- `oauth.verify_ssl`
  - 保存后立即影响 OAuth 控制平面请求和 OAuth 数据面代理
  - 默认值为 `false`
  - 关闭时不校验 HTTPS 证书，便于本地代理或抓包代理场景
- `api_keys.enabled`
  - 归类为“API Key 管理”
  - 页面修改后自动生效
  - 保存后立即影响后台顶部 API Key 管理页签是否显示
  - 保存后立即影响数据平面 `/v1/chat/completions`、`/v1/responses`、`/v1/messages` 和 `/v1/models` 是否要求下游携带 API Key
  - 默认值为 `false`

运行时内存状态补充：

- `Application`
  - 在保存日志配置后可重新装配 logger handler
  - 每次访问日志写入前读取当前 `client_ip.*` 配置解析客户端 IP
- `WebController`
  - 渲染后台页面时读取当前 `oauth.enabled`，用于决定是否输出 OAuth 顶层导航项
  - 渲染后台页面时读取当前 `api_keys.enabled`，用于决定是否输出 API Key 管理顶层导航项
- `ProxyController`
  - 每次数据面请求读取当前 `client_ip.*` 配置解析客户端 IP
  - 解析结果用于白名单、模型权限、请求统计、访问日志关联和 LLM trace
  - 每次数据面请求读取当前 `api_keys.enabled`，开启时要求有效 API Key
  - 开启 API Key 管理时会从转发给上游的 header 中移除下游 `Authorization` 和 `X-API-Key`，避免泄露下游 key
  - 请求完成回调写日志时会带上 `api_key_id`，用于同步累加 key 级用量
- `ModelCatalogService`
  - 每次用户 / API Key 权限管理读取当前 Provider 配置模型和 Codex / Claude OAuth 可用模型
  - 不缓存模型目录，OAuth 模型变化后管理端重新加载即可出现在权限选择列表
- `CodexOAuthService`
  - 每次 token / quota / models 请求读取当前 `oauth.proxy_mode`、`oauth.proxy` 与 `oauth.verify_ssl`
  - 维护 OAuth PKCE 临时会话、Codex 账号配额冷却状态与认证文件配额刷新锁
  - 在 `data/oauth/codex/.state/auth_files.json` 持久化认证文件配额、最近一次模型代理状态与最近成功认证文件
- `ClaudeOAuthService`
  - 每次 token / models 请求读取当前 `oauth.proxy_mode`、`oauth.proxy` 与 `oauth.verify_ssl`
  - 维护 OAuth PKCE 临时会话
  - 认证文件保存在 `data/oauth/claude/`
  - 在 `data/oauth/claude/.state/auth_files.json` 持久化最近一次模型代理状态与最近成功认证文件
- `CodexProxyService`
  - 每次 Codex 数据面请求读取当前 `oauth.proxy_mode`、`oauth.proxy` 与 `oauth.verify_ssl`
- `ClaudeProxyService`
  - 每次 Claude 数据面请求读取当前 `oauth.proxy_mode`、`oauth.proxy` 与 `oauth.verify_ssl`

### 3.3 Provider Runtime Contract

Provider 公共配置字段只有：

- `name`
- `api`
- `source_format`
- `api_key`
- `auth_group`
- `proxy_mode`
- `proxy`
- `timeout_seconds`
- `max_retries`
- `verify_ssl`
- `model_list`
- `hook`

其中：

- `source_format`
  - 上游真实协议
- `proxy_mode`
  - 支持 `direct` / `system` / `custom`
  - `direct` 明确绕开环境代理，`system` 使用进程环境代理，`custom` 使用 `proxy`
- `proxy`
  - 仅在 `proxy_mode=custom` 且非空时生效，`custom` 空值按直连执行
  - 自定义代理 URL 中 userinfo 的账号密码会在保存时规范化转义

历史配置载入时会自动删除 `target_format`、`target_formats` 和 `transport` 并回写配置文件，用于兼容迁移窗口内的旧配置。
历史 Provider / OAuth 配置缺少 `proxy_mode` 时也会在载入阶段自动回写：有 `proxy` 的配置补为 `custom`，没有 `proxy` 的配置补为 `direct`。

没有公共 `transport` 或 `stream_format` 字段；Provider 上游传输固定由 HTTP executor 处理。

Hook 运行时上下文还会暴露最小重试状态：

- `retry`
- `auth_group_name`
- `auth_entry_id`
- `last_status_code`
- `last_error_type`

其中 `last_error_type` 使用 `HookErrorType` 枚举，当前值为：

- `TIMEOUT`
- `CONNECTION_ERROR`
- `TRANSPORT_ERROR`

### 3.4 Internal Stream Detection

流式识别完全是内部实现细节：

- HTTP `Content-Type = text/event-stream`
  - 按 SSE JSON 处理
- HTTP `Content-Type` 含 `ndjson/jsonl`
  - 按 NDJSON 处理
- 其他
  - 按非流式处理
- 如果请求声明为流式，但首块看起来像 SSE
  - 触发首块探测兜底

这层能力保留在 executor / decoder 中，不暴露给用户配置。

### 3.5 Runtime Trace Logging

当 `logging.llm_request_debug_enabled = true` 时：

- 应用会写入 `logs/llm_request_trace.log`
- 与 `app.log`、`access.log` 分离
- 采用相同的滚动策略：
  - `RotatingFileHandler`
  - `maxBytes = 10 MiB`
  - `backupCount = 3`

### 3.6 Control-Plane Model Fetching And Testing

Provider 编辑页包含两条控制平面上游探测链路：

- `GET /api/providers/fetch-models`
- `POST /api/providers/test-models`

链路如下：

```text
provider editor form snapshot
  -> controller
  -> auth header resolve (api_key or auth_group + auth_entry)
  -> ProviderRuntimeFactory
  -> header_hook
  -> translator.translate_request()
  -> request_guard
  -> usage request enrichment when protocol supports it
  -> executor
  -> decoder
  -> translator.translate_response(openai_chat benchmark view)
  -> metric collector
  -> modal result table
```

模型拉取链路如下：

```text
provider editor form snapshot
  -> controller
  -> auth header resolve (api_key or auth_group + auth_entry)
  -> model endpoint inference
  -> upstream fetch (/v1/models or /models)
  -> fetched model picker
  -> provider form model table
```

行为约束：

- 这两条都是控制平面能力，不经过下游 `/v1/chat/completions` / `/v1/responses` / `/v1/messages`
- 测试模型列表中的模型值按真实上游模型 ID 处理，不执行 `{provider_name}/` 路由前缀裁剪
- 两条链路都会使用 Provider 表单快照中的 `proxy_mode`、`proxy` 和 `verify_ssl`
- Provider 编辑页的 `model_list` 采用表格编辑，并以当前前端行状态作为唯一数据源
- 只应用 request-side hook：
  - `header_hook`
  - `request_guard`
- 不应用 `response_guard`
- `auth_group` 模式下：
  - 拉取模型必须显式选择 `auth_entry`
  - 测试模型也必须显式选择 `auth_entry`
  - 两者都不经过 `AuthGroupManager.acquire()`
  - 两者都不写运行态冷却、并发、配额
- 首字延迟仅在真实流式首个正文或推理增量到达时记录
- TPS 仅在拿到 completion usage 后计算
- 如果上游成功但未返回 usage：
  - `available = true`
  - `tps = null`
- 批量测试会先锁定本次选中的目标行，再按顺序逐条请求
- 每一条测试结果一返回就立即回填到对应表格行
- 批量测试属于当前页面会话内行为；页面刷新或离开后，尚未开始的后续测试不会继续执行

能力边界：

- 数据平面主代理链路独立于 Provider 编辑页联通性测试链路
- Provider 编辑页提供控制平面的上游模型拉取与性能测试能力

### 3.7 Control-Plane OAuth Management

OAuth 管理页在 `oauth.enabled=true` 时提供顶层 `OAuth` 导航项，并在页面内提供 `Codex` 与 `Claude` 子 tab。`oauth.enabled` 默认关闭，因此新配置默认不会展示 OAuth 页签。

页面与 API：

- 页面
  - `GET /oauth`
- API
  - `POST /api/oauth/codex/session`
  - `POST /api/oauth/codex/callback`
  - `GET /api/oauth/codex/models`
  - `POST /api/oauth/codex/models`
  - `DELETE /api/oauth/codex/models/<model_id>`
  - `GET /api/oauth/codex/auth-files`
  - `POST /api/oauth/codex/auth-files/export`
  - `POST /api/oauth/codex/auth-files/import`
  - `DELETE /api/oauth/codex/auth-files/<name>`
  - `GET /api/oauth/codex/auth-files/<name>/quota`
  - `POST /api/oauth/claude/session`
  - `POST /api/oauth/claude/callback`
  - `GET /api/oauth/claude/models`
  - `POST /api/oauth/claude/models`
  - `DELETE /api/oauth/claude/models/<model_id>`
  - `GET /api/oauth/claude/auth-files`
  - `POST /api/oauth/claude/auth-files/export`
  - `POST /api/oauth/claude/auth-files/import`
  - `DELETE /api/oauth/claude/auth-files/<name>`

Codex OAuth 登录链路如下：

```text
OAuth Codex tab
  -> create session
  -> generate PKCE verifier / challenge and state
  -> return auth.openai.com authorization URL
  -> user opens URL and signs in
  -> user pastes full callback URL
  -> token exchange
  -> write data/oauth/codex/*.json
  -> list / manage local Codex model IDs
  -> list auth file token/status/quota snapshot
  -> optional quota refresh to chatgpt.com/backend-api/wham/usage
  -> skip duplicate quota refresh when the same auth file is already refreshing
  -> persist quota snapshot or quota error
```

Claude OAuth 登录链路如下：

```text
OAuth Claude tab
  -> create session
  -> generate PKCE verifier / challenge and state
  -> return claude.ai authorization URL
  -> user opens URL and signs in
  -> user pastes full callback URL or code#state
  -> token exchange
  -> write data/oauth/claude/*.json
  -> list / manage local Claude model IDs
  -> list auth file token status
```

运行时与存储约束：

- OAuth state、PKCE verifier 只保存在进程内内存中
- 临时 OAuth 会话 TTL 为 10 分钟
- 认证文件保存在 `data/oauth/codex/`
- Claude 认证文件保存在 `data/oauth/claude/`
- 删除认证文件不会直接 `unlink`；Codex / Claude 都会移动到各自认证目录下的 `deleted/` 子目录
- 删除归档文件名前缀为删除时的本地年月日时分秒，例如 `20260605123045_<原文件名>`；如果目标文件已存在，会在扩展名前追加 `-1`、`-2`、`-3`
- Codex 认证文件名沿用 CLIProxyAPI 规则：普通账号为 `codex-{email}-{plan}.json`，team 账号为 `codex-{account_id_sha256前8位}-{email}-team.json`
- Claude 认证文件名沿用 CLIProxyAPI 规则：`claude-{email}.json`
- Codex 模型目录缓存在 `data/oauth/codex/models.json`，文件内容只保存模型 ID 字符串数组
- Claude 模型目录缓存在 `data/oauth/claude/models.json`，文件内容只保存模型 ID 字符串数组
- 认证文件的最近配额、配额错误、数据面使用状态和最近成功认证文件保存在 `data/oauth/codex/.state/auth_files.json`
- Claude 认证文件最近一次数据面使用状态和最近成功认证文件保存在 `data/oauth/claude/.state/auth_files.json`
- 认证文件列表会把候选筛选结果和触发原因作为状态显示；最近一次数据面错误摘要单独作为信息显示
- OAuth 页面认证文件列表按名称排序、每页最多展示 50 个，支持多文件导入、全选后批量刷新额度、ZIP 导出和批量归档删除
- OAuth 页面导入认证文件时支持选择多个 JSON 文件或多个 ZIP 包；ZIP 必须是导出 API 生成的根目录 JSON 文件结构；每个 JSON 都会校验 provider 类型、access token、email 和过期时间，合法才写入认证目录
- OAuth 页面导入完成后按导入结果 toast 提示成功数量和失败数量
- OAuth 页面导出选中认证文件时调用导出 API，单个文件也会以 ZIP 下载
- OAuth 页面删除认证文件前会用气泡确认，确认后调用删除 API 把文件移动到 `deleted/`
- Codex 模型 ID 由用户在 OAuth 页面手动维护，默认列表为空
- Claude 模型 ID 由用户在 OAuth 页面手动维护，默认列表为空
- OAuth 页面提供 `router-for-me/models` 仓库的 `models.json` 与 `https://models.router-for.me/models.json` 作为外部参考链接，不自动拉取
- Codex 查询配额时如果认证文件 access token 已过期，且存在 refresh token，会先刷新认证文件
- Claude OAuth 数据面请求前如果认证文件 access token 已过期，且存在 refresh token，会先刷新认证文件
- Codex / Claude 候选列表仍会按请求重建；默认按认证文件修改时间倒序排列，但最近一次真实请求成功的认证文件如果未被过滤，会被提升为第一候选
- 同一个认证文件的配额刷新使用进程内非阻塞锁；重复刷新请求会直接返回跳过结果，不重复访问 Codex 上游
- 如果认证文件 access token 已过期且缺少 refresh token，请求候选筛选不会直接跳过；系统会先用当前 access token 尝试请求一次，再按上游返回的认证、配额或其他错误决定后续状态
- 配额刷新会同步内存冷却状态：Codex 窗口耗尽时冷却该认证文件，恢复可用时立即清除冷却
- Codex 数据面请求成功后，如果本地配额快照中的 Codex 窗口重置时间已经到期，会最佳努力刷新该认证文件的前端配额快照；刷新失败不会阻断本次模型响应
- 认证类错误会持久显示为认证失败并参与候选过滤；重新 OAuth 登录、token 刷新成功或后续真实请求成功后会清除该状态
- OAuth 顶层导航项是否显示由系统设置中的 `oauth.enabled` 控制
- token 交换、token 刷新、Codex 配额查询与 OAuth 数据面代理会使用系统设置中的 `oauth.proxy_mode`、`oauth.proxy` 和 `oauth.verify_ssl`
- Codex 数据面请求在上游返回错误或请求失败时，会记录当前认证文件信息并尝试下一个候选认证文件，直到成功或候选耗尽
- Claude OAuth 数据面请求会在转发 Anthropic Messages 前按 CPA 请求方式重签已有 Claude Code billing header 的 `cch`
- 普通 Provider 如果 `source_format=claude_chat`，也会在上游 body 已有 Claude Code billing header 时重签 `cch`；不会主动生成 billing header
- Claude 数据面请求在上游返回错误或请求失败时，会记录当前认证文件信息并尝试下一个候选认证文件，直到成功或候选耗尽
- 出站 HTTP 请求遇到代理风险确认页时，会自动确认一次并重试原请求；自动确认失败或重试后仍被拦截时，返回 `proxy_warning_required` 和确认页 URL
- Codex / Claude 上游返回 401 或认证类错误时，会将当前认证文件标记为认证失败，后续请求优先跳过
- OAuth 登录、文件、配额与模型目录管理属于控制平面
- Codex / Claude 模型代理属于 `/v1/*` 数据平面，但不进入 Provider 路由或 Auth Group 选择流程

### 3.8 Control-Plane API Key Management

API Key 管理页在 `api_keys.enabled=true` 时提供顶层 `API Key 管理` 导航项。`api_keys.enabled` 默认关闭，因此新配置默认不会展示 API Key 页签，也不会要求下游请求携带 key。

页面与 API：

- 页面
  - `GET /api-keys`
- API
  - `GET /api/api-keys`
  - `POST /api/api-keys`
  - `GET /api/api-keys/<key_id>`
  - `PUT /api/api-keys/<key_id>`
  - `DELETE /api/api-keys/<key_id>`
  - `POST /api/api-keys/<key_id>/toggle`

创建与存储约束：

- 新建 key 使用 `sk-` 前缀随机字符串
- 明文 key 会保存在 SQLite 中，管理端列表可点击复制图标复制，复制后短暂在 key 旁显示明文气泡
- 创建和编辑都可以设置名称、模型权限、总 token 使用上限和启用状态
- SQLite `api_keys` 表保存：
  - `api_key`
  - `key_hash`
  - `key_prefix`
  - `key_suffix`
  - `enabled`
  - `model_permissions`
  - `token_limit_k`
  - 累计请求数与 token 用量
  - 创建、更新和最近使用时间
- `model_permissions='*'` 表示允许全部模型
- 显式模型权限以 JSON 数组保存，Provider 模型使用 `{provider}/{model}` 路由 key，OAuth 模型使用原始模型名
- `token_limit_k=NULL` 表示不限额；非空时单位为 k，最小有效值为 `1`，按 `api_keys.total_tokens` 计量
- Provider 模型变化后，`Application.reload_providers()` 会按当前模型权限目录同步清理 API Key 显式授权中已经不存在的模型

数据面 API Key 鉴权链路如下：

```text
/v1 request
  -> resolve client IP
  -> optional Chat whitelist user lookup
  -> optional API Key lookup by hash
  -> request body model validation
  -> provider / OAuth model lookup
  -> user model permission check
  -> API Key model permission check
  -> API Key token limit check
  -> upstream proxy
  -> request log insert
  -> API Key usage counter update
```

行为约束：

- `api_keys.enabled=false`
  - 数据面不要求下游 key
  - API Key 管理页签不显示
- `api_keys.enabled=true`
  - `/v1/chat/completions`、`/v1/responses`、`/v1/messages` 和 `/v1/models` 必须携带有效且启用的 key
  - 支持 `Authorization: Bearer sk-...`
  - 支持 `X-API-Key`
  - 缺少 key 返回 `missing_api_key`
  - key 不存在或已停用返回 `invalid_api_key`
  - key 无权访问请求模型返回 `api_key_model_not_allowed`
  - key 的累计 `total_tokens` 已达到 `token_limit_k * 1000` 时返回 `api_key_token_limit_exceeded`
- 同时开启 Chat 白名单时：
  - 白名单仍按客户端 IP 解析用户
  - 用户模型权限和 API Key 模型权限都必须允许目标模型
  - `/v1/models` 只返回两者交集
- 请求完成后：
  - `request_logs.api_key_id` 记录本次使用的 key
  - `api_keys.total_request_count`
  - `api_keys.prompt_tokens`
  - `api_keys.completion_tokens`
  - `api_keys.total_tokens`
  - `api_keys.last_used_at`
  - 这些字段与请求日志在同一 SQLite 事务中更新

## 4. Development View

### 4.1 Directory Responsibilities

- `src/presentation/`
  - HTTP route、管理页面、API controller
- `src/services/`
  - 代理主流程和业务服务
- `src/config/`
  - 配置加载、schema、provider runtime
- `src/executors/`
  - HTTP executor
- `src/proxy_core/`
  - decoder、encoder、shared contracts
- `src/translators/`
  - protocol translators and shared reasoning helpers
- `src/hooks/`
  - hook contracts

### 4.2 Key Files

- [src/services/proxy_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/proxy_service.py)
  - 主代理 orchestration
- [src/services/settings_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/settings_service.py)
  - 系统设置保存与生效边界
- [src/services/provider_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/provider_service.py)
  - Provider 配置管理、复制、排序、JSON 导入导出和重载触发
- [src/services/user_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/user_service.py)
  - 用户管理、模型权限、IP 缓存、JSON 导入导出和批量删除
- [src/services/log_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/log_service.py)
  - 请求日志查询、统计聚合、Excel 数据读取和日聚合统计 JSON 导入导出
- [src/repositories/log_repository.py](/root/.ww/code/002llm/000LLM_Proxy/src/repositories/log_repository.py)
  - 请求日志、日聚合统计、筛选查询和日聚合统计累加导入
- [src/services/api_key_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/api_key_service.py)
  - API Key 生成、hash 鉴权、模型权限、token 上限和 key 级用量管理
- [src/services/model_catalog_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/model_catalog_service.py)
  - 汇总 Provider 配置模型与 Codex / Claude OAuth 可用模型，供用户和 API Key 模型权限共用
- [src/repositories/api_key_repository.py](/root/.ww/code/002llm/000LLM_Proxy/src/repositories/api_key_repository.py)
  - API Key 持久化、列表排序和累计用量字段
- [src/services/codex_oauth_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/codex_oauth_service.py)
  - Codex OAuth PKCE、token 文件、本地模型 ID 目录与配额查询
- [src/services/claude_oauth_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/claude_oauth_service.py)
  - Claude OAuth PKCE、token 文件、本地模型 ID 目录与认证文件管理
- [src/services/oauth_auth_file_archive.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/oauth_auth_file_archive.py)
  - OAuth 认证文件 ZIP 导出、JSON/ZIP 导入展开与删除归档移动
- [src/services/codex_proxy_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/codex_proxy_service.py)
  - Codex OAuth 数据面代理与账号配额切换
- [src/services/claude_proxy_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/claude_proxy_service.py)
  - Claude OAuth 数据面代理与账号切换
- [src/presentation/oauth_controller.py](/root/.ww/code/002llm/000LLM_Proxy/src/presentation/oauth_controller.py)
  - OAuth 管理 API
- [src/presentation/api_key_controller.py](/root/.ww/code/002llm/000LLM_Proxy/src/presentation/api_key_controller.py)
  - API Key 管理 API
- [src/config/provider_config.py](/root/.ww/code/002llm/000LLM_Proxy/src/config/provider_config.py)
  - Provider schema
- [src/executors/registry.py](/root/.ww/code/002llm/000LLM_Proxy/src/executors/registry.py)
  - HTTP executor
- [src/proxy_core/decoders.py](/root/.ww/code/002llm/000LLM_Proxy/src/proxy_core/decoders.py)
  - 流式解码
- [src/proxy_core/encoder.py](/root/.ww/code/002llm/000LLM_Proxy/src/proxy_core/encoder.py)
  - 下游编码
- [src/translators/registry.py](/root/.ww/code/002llm/000LLM_Proxy/src/translators/registry.py)
  - 4x4 translator registry
- [src/translators/reasoning_utils.py](/root/.ww/code/002llm/000LLM_Proxy/src/translators/reasoning_utils.py)
  - reasoning / thinking 语义映射与 OpenAI 兼容 reasoning 字段提取
- [src/presentation/templates/providers.html](/root/.ww/code/002llm/000LLM_Proxy/src/presentation/templates/providers.html)
  - Provider 页面与 `source_format` / Auth Group 编辑
- [src/presentation/templates/settings.html](/root/.ww/code/002llm/000LLM_Proxy/src/presentation/templates/settings.html)
  - 系统设置页面与帮助说明
- [src/presentation/templates/api_keys.html](/root/.ww/code/002llm/000LLM_Proxy/src/presentation/templates/api_keys.html)
  - API Key 管理页面、创建/编辑弹窗、模型权限选择、Key 复制气泡和用量表格
- [src/presentation/templates/oauth.html](/root/.ww/code/002llm/000LLM_Proxy/src/presentation/templates/oauth.html)
  - OAuth 管理页面与 Codex / Claude 子 tab

## 5. Physical View

部署上是单体服务：

- 一个 Flask 应用
- 一个配置文件
- 一个 SQLite 数据库，保存用户、请求日志、日聚合统计与 API Key
- 一组滚动日志文件
- 一组本地 OAuth 认证文件
- 一组本地 OAuth 模型目录缓存
- 多个 provider 指向多个真实上游
- 下游统一接入这个代理

```text
Client / Agent / IDE
        |
        v
    LLM Proxy
        |
        +--> OpenAI Chat upstream
        +--> OpenAI Responses upstream
        +--> Claude Messages upstream
        +--> Codex upstream
        +--> auth.openai.com / chatgpt.com OAuth control-plane endpoints
        +--> claude.ai / api.anthropic.com OAuth control-plane endpoints
```

## 6. Scenarios

### 6.1 OpenAI Chat Downstream -> Responses Upstream

```mermaid
sequenceDiagram
    participant Client
    participant Controller
    participant Service
    participant Translator
    participant Executor

    Client->>Controller: POST /v1/chat/completions
    Controller->>Controller: 按 client_ip.* 解析客户端 IP
    opt api_keys.enabled=true
        Controller->>Controller: 校验 API Key hash、key 模型权限和 token 上限
    end
    Controller->>Service: proxy_request()
    Service->>Translator: openai_responses -> openai_chat
    Translator-->>Service: translated upstream request
    Service->>Executor: execute HTTP request
    Executor-->>Service: stream events
    Service->>Translator: translate stream events
    Translator-->>Service: openai_chat chunks
    Service->>Service: 编码下游块并移除空 delta.tool_calls
    Service-->>Controller: SSE response
    Controller-->>Client: chat.completion.chunk stream
```

### 6.2 Plain Codex Model -> Codex OAuth Backend

```mermaid
sequenceDiagram
    participant Client
    participant Controller
    participant CodexOAuth
    participant CodexProxy
    participant ChatGPT

    Client->>Controller: POST /v1/chat/completions model=gpt-5-codex
    Controller->>Controller: Provider 未命中后查 Codex 模型目录
    Controller->>CodexOAuth: iter_auth_candidates_for_model()
    CodexOAuth->>CodexOAuth: 过滤认证失败/冷却文件，并优先最近成功认证文件
    Controller->>CodexProxy: proxy_request()
    CodexProxy->>ChatGPT: POST /backend-api/codex/responses
    Note over CodexProxy,ChatGPT: 对齐 Codex backend 要求：stream=true、store=false、parallel_tool_calls=true、include encrypted content，并移除不支持字段
    alt 代理风险确认页
        ChatGPT-->>CodexProxy: 302 proxycontrolwarn
        CodexProxy->>ChatGPT: GET warning page and check endpoint
        CodexProxy->>ChatGPT: retry original POST once
        alt 自动确认失败或重试后仍被拦截
            CodexProxy-->>Controller: proxy_warning_required + confirmation_url
            Controller-->>Client: error response
        end
    else 账号配额耗尽
        ChatGPT-->>CodexProxy: 429 usage_limit_reached
        CodexProxy->>CodexOAuth: mark_auth_file_quota_exhausted()
        CodexProxy->>CodexOAuth: record_auth_file_failure()
        CodexProxy->>ChatGPT: 使用下一个认证文件重试
    else 账号认证失败
        ChatGPT-->>CodexProxy: 401 authentication_error
        CodexProxy->>CodexOAuth: record_auth_file_failure()
        CodexProxy->>ChatGPT: 使用下一个认证文件重试
    end
    ChatGPT-->>CodexProxy: Responses SSE
    CodexProxy->>CodexOAuth: record_auth_file_success()
    CodexOAuth->>CodexOAuth: 记录最近成功认证文件
    opt 本地配额快照 reset_at 已到期
        CodexOAuth->>ChatGPT: GET /backend-api/wham/usage
        CodexOAuth->>CodexOAuth: 更新认证文件配额快照
    end
    CodexProxy-->>Controller: 下游协议响应
    Controller-->>Client: OpenAI-compatible response
```

### 6.2 Claude Downstream -> OpenAI Chat Upstream

```mermaid
sequenceDiagram
    participant Client
    participant Controller
    participant Service
    participant Translator
    participant Executor

    Client->>Controller: POST /v1/messages
    Controller->>Service: proxy_request()
    Service->>Translator: openai_chat -> claude_chat
    Translator-->>Service: upstream chat request
    Service->>Executor: execute HTTP request
    Executor-->>Service: chat SSE stream
    Service->>Translator: translate to claude events
    Translator-->>Service: message_start/content_block_delta/message_stop
    Service-->>Client: Claude-style SSE
```

## 7. Runtime Boundaries

### 7.1 Protocol Families

当前协议族面向以下目标客户端：

- OpenCode
- Codex
- Claude Code
- Cherry Studio

系统内置协议族聚焦 OpenAI Chat Completions、OpenAI Responses / Codex 与 Claude Messages。Gemini / Antigravity 等协议面不属于当前内置协议族范围。

### 7.2 Stream Format Ownership

流格式判断属于代理内部责任。

用户只需要清楚：

- 上游是什么协议
- 下游要暴露成什么协议集合

上游到底是 SSE、NDJSON 还是非流式，由 executor / decoder 自动判断。
