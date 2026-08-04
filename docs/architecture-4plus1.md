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
  - `POST /v1/images/generations`
  - `POST /v1/images/edits`
  - `GET /v1/models`
  - `OPTIONS /v1/*` CORS 预检

这个版本不保留 Gemini / Antigravity。配置加载阶段会清理少量历史废弃字段并回写配置文件；除此之外不再继续扩展旧字段兼容逻辑。

控制平面除了 Provider、Auth Group、用户、统计和系统设置，还可以：

- 在启用 `oauth.enabled` 后提供 OAuth 管理入口，用于生成、查看、启停、导入、导出和归档删除 CLI/OAuth 类本地认证文件
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
  -> strip downstream Authorization from upstream headers
  -> auth header resolve (api_key or auth_group + auth_entry)
  -> header_hook
  -> translator.translate_request()
  -> request_guard
  -> usage request enrichment / provider protocol signing
  -> executor
  -> decoder
  -> translator.translate_response()
  -> response_guard
  -> encoder
  -> prefetch first non-empty encoded downstream bytes (stream only)
  -> downstream response
  -> response outcome finalization
```

Provider 开启 `force_upstream_stream=true` 且下游请求未开启流式时，链路在 decoder 之后走聚合分支：

```text
decoder
  -> upstream stream translator -> OpenAI Chat chunk accumulator
  -> target nonstream translator
  -> response_guard
  -> JSON encoder
  -> downstream response
```

该分支向上游发送 `stream=true`，在代理进程内完成响应聚合，向下游只发送一个非流式 JSON 响应。下游明确发送 `stream=true` 时继续使用标准流式链路。

流式响应以首个非空、已经完成目标协议编码的下游字节为提交边界：

- 普通 Provider 提交前的 transport 异常会关闭当前上游响应，并在 `max_retries` 定义的最大尝试次数范围内重新执行上游请求
- 普通 Provider 的最大尝试次数耗尽后返回结构化 `502` 错误响应
- Codex / Claude OAuth 提交前的 transport 异常会记录当前认证文件失败并尝试下一个候选认证文件
- 提交后的 transport 异常会编码为当前下游协议的流内错误事件，当前请求不执行透明重试
- 目标协议终止事件已经发出时，后续 HTTP framing 异常保持当前流的逻辑完成状态
- 客户端取消会关闭上游响应并释放请求占用的运行时资源，不触发成功完成统计

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
  - 处理 `/v1/images/generations` 与 `/v1/images/edits`，图片模型使用 Codex OAuth 图片模型目录和默认图片模型
  - 构造标准错误体
- `DataPlaneCors`
  - 只为 `/v1/*` 数据平面添加 CORS 响应头
  - 直接处理 `OPTIONS /v1/*` 预检请求
- `WebController`
  - 提供 Provider、用户、API Key、统计与系统设置页面
  - 暴露统计汇总、用户用量汇总、请求明细、当前页签 Excel 导出与统计迁移 JSON 导入导出接口
  - 在 `oauth.enabled=true` 时显示 OAuth 顶层导航入口
  - 在 `api_keys.enabled=true` 时显示 API Key 管理顶层导航入口
  - 暴露系统设置读取与保存接口
- `ProviderService`
  - 维护 Provider 配置的创建、复制、编辑、删除、启停、排序和批量删除
  - 在配置写入和管理 API 输出时校验本机 hook 文件；不存在、绝对路径或跳出 `hooks/` 的 hook 字段为空
  - 按选中 Provider 导出 JSON，导出包包含被选 Provider、它们引用到的 Auth Group、Auth Entry 运行态和用量桶表项
  - 导入 Provider JSON 时为同名 Provider 和 Auth Group 生成数字后缀名称，并同步重写导入 Provider 的 `auth_group` 引用
  - 导入 Provider JSON 时按 Auth Group 重命名结果同步写入 Auth Entry 运行态和用量桶表项
- `UserService`
  - 维护用户、IP 白名单状态和模型权限
  - 支持用户迁移 JSON 导入导出与批量删除；导出包包含用户表配置
  - 导入用户迁移 JSON 时按 IP 创建或更新用户
- `LogService`
  - 读取请求日志、统计汇总、用户用量汇总和筛选项
  - 支持按筛选条件导出统计迁移 JSON，迁移包包含请求明细和日聚合统计
  - 导入请求明细时按明细业务字段识别重复行；导入日聚合统计时按日期、IP、请求模型和响应模型累加数值
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
  - OAuth 模型从 Codex / Claude OAuth 服务读取当前可用模型目录，Codex 图片模型同时进入权限目录
  - 供用户模型权限与 API Key 模型权限的选择、保存校验、展示计数和同步清理共用
- `OAuthController`
  - 暴露 Codex / Claude OAuth 登录、回调提交、认证文件列表、启停、JSON/ZIP 导入、ZIP 导出、归档删除与 Codex 配额刷新接口
  - 暴露 Codex 文本模型、图片模型、默认图片模型和添加内置模型目录接口
- `CodexOAuthService`
  - 生成 Codex OAuth PKCE 授权链接
  - 使用回调 URL 换取 token 并写入本地认证文件
  - 导入认证文件时支持多 JSON 和多 ZIP，逐个校验合法内容后写入
  - 导出选中认证文件时始终打包为 ZIP
  - 删除本地认证文件时移动到 `data/oauth/codex/deleted/`，并同步清理该文件的本地状态
  - 读取认证文件状态，并使用当前 access token 查询 Codex 配额
  - Codex 配额查询优先使用认证文件 `proxy_url`，其后使用全局 OAuth 代理设置
  - token 交换、token 刷新与配额查询遇到代理风险确认页时，会走统一自动确认重试流程
  - 按认证文件名限制同一时刻只有一个配额刷新请求会真实访问上游
  - 持久化认证文件人工禁用状态、最近一次配额快照、配额刷新错误、最近成功认证文件与 Codex 模型代理使用状态
  - 启动 Codex 配额后台刷新任务，每 5 小时刷新一轮可查询认证文件，认证文件之间间隔 10 秒
  - 维护本地 Codex OAuth 文本模型目录、图片模型目录和默认图片模型
  - 内置常用 Codex 文本模型和图片模型 ID；添加内置模型只把缺失的内置 ID 加回本地目录，保留用户自行添加的模型 ID
  - 按本地模型目录、人工禁用状态、本地冷却、认证失败状态和最近成功认证文件提供 Codex 请求候选账号
- `ClaudeOAuthService`
  - 生成 Claude OAuth PKCE 授权链接
  - 使用回调 URL 或手动粘贴的 `code#state` 换取 token 并写入本地认证文件
  - 列出、JSON/ZIP 导入、ZIP 导出和归档删除 `data/oauth/claude/*.json`
  - 维护本地手动 Claude OAuth 模型目录
  - 持久化认证文件人工禁用状态与最近一次模型代理状态
  - 按本地模型目录、人工禁用状态、认证失败状态和最近成功认证文件提供 Claude 请求候选账号
  - token 交换与 token 刷新遇到代理风险确认页时，会走统一自动确认重试流程
- `CodexProxyService`
  - 代理下游直接使用的 Codex 普通模型名
  - 使用 `data/oauth/codex/*.json` 中的 OAuth access token 请求 Codex backend
  - 按默认图片模型为普通 Codex 请求补齐 `image_generation` 工具配置
  - 将 OpenAI Images 兼容请求包装成 Codex `image_generation` 工具调用
  - 遇到账号配额耗尽时标记临时冷却、刷新该认证文件配额快照并尝试下一个账号
  - 将每个认证文件最近一次数据面成功或失败结果写回 OAuth 状态
  - 按首个非空目标协议字节区分流提交前后的 transport 失败
  - 流提交后的 transport 失败会发送下游协议错误事件并记录当前认证文件失败
  - 缺少 `response.completed` 或 `response.done` 的 EOF 按未完成流处理
  - 客户端取消会关闭上游响应，不触发成功完成统计，也不更新认证文件成功或失败状态
- `ClaudeProxyService`
  - 代理下游直接使用的 Claude 普通模型名
  - 使用 `data/oauth/claude/*.json` 中的 OAuth access token 请求 Anthropic Messages
  - 按 CPA / CLIProxyAPI 请求方式补齐 Claude Code OAuth headers，并在存在 billing header 时重签 `cch`
  - 将每个认证文件最近一次数据面成功或失败结果写回 OAuth 状态
  - 按首个非空目标协议字节区分流提交前后的 transport 失败
  - 流提交后的 transport 失败会发送下游协议错误事件并记录当前认证文件失败
  - 缺少 `message_stop` 的 EOF 按未完成流处理
  - 客户端取消会关闭上游响应，不触发成功完成统计，也不更新认证文件成功或失败状态
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
  - Provider 开启 `force_upstream_stream` 且下游非流式时，强制上游请求使用流式并选择聚合响应路径
  - 在下游流提交前管理 Provider 上游最大尝试次数
  - 对 `source_format=claude_chat` 的 Provider，在上游 body 已有 Claude Code billing header 时重签 `cch`
  - 在开启 `logging.llm_request_debug_enabled` 时输出独立 trace
- `ProxyResponseBuilder`
  - 构建非流式响应和流式响应
  - 将强制上游流式场景的上游事件聚合为完整响应，再编码为非流式 JSON
  - 预取首个非空目标协议字节并建立流提交边界
  - 跟踪协议终止事件、上游 transport 失败和客户端取消
  - 按下游协议编码流内错误事件并结算上游响应资源
  - 在流实际结束后记录成功、失败或客户端取消结果
- `ProviderModelTestService`
  - 复用 translator / executor / request-side hook
  - 按当前 Provider 表单快照直连上游测试模型可用性、首字延迟与 TPS
  - 在协议支持时显式请求 usage 返回
  - 批量测试按前端当前选择的模型行逐条执行并逐条回填结果
- `ModelDiscoveryService`
  - 按当前 Provider 表单快照拉取上游模型列表
  - Hook 实现 `fetch_models` 时使用 Hook 返回的模型列表或模型 payload
  - Hook 返回 `None` 时使用 `/v1/models` 与 `/models` 候选端点探测
- `SettingsService`
  - 维护 `server`、`admin`、`oauth`、`api_keys` 与 `logging`
  - 管理立即生效项与重启生效项的边界
- `ProviderRuntimeFactory`
  - 负责临时 / 正式 Provider 运行时对象构建
  - 统一 hook 路径校验、懒加载、文件签名检查与弱引用缓存
  - Hook 路径固定相对项目根目录 `hooks/`
- `ExecutorRegistry`
  - 负责 HTTP 上游连接
  - 统一处理 Provider 出站请求中的代理风险确认页自动确认与一次重试
- `Decoder`
  - 将上游流拆成统一事件
- `TranslatorRegistry`
  - 负责协议适配
  - 为上游强制流式聚合提供各上游协议到 OpenAI Chat chunk 的归一化入口
  - 维护 OpenAI Chat `reasoning_effort`、OpenAI Responses `reasoning.effort` 与 Claude `thinking` 的请求语义映射
  - 将 OpenAI Chat 上游 reasoning 内容转换为下游协议对应的 thinking / reasoning 输出
- `Encoder`
  - 将统一 chunk 编码成下游协议
- `Hook`
  - 负责 header、guard、成功响应清洗和模型拉取扩展
  - 按配置路径懒加载，不扫描 `hooks/` 目录
  - 每次调用扩展点前检查 Hook 文件签名；文件新增或内容变化后按当前文件重新加载
  - Hook 文件删除后，已加载实例继续可用；从未成功加载过的路径保持 no-op
  - 实际 Hook 实例使用弱引用缓存，没有 Provider 或控制平面临时对象引用时可被回收
  - `request_guard` 运行在协议转换之后，用于上游前的厂商私有参数适配
  - `fetch_models` 运行在控制平面的 Provider 模型拉取链路中
  - 内置上游思考参数 Hook 位于 `hooks/openai_reasoning_compat.py`
  - MiniMax、DeepSeek、GLM / Z.AI、Qwen / DashScope 各有独立处理类和单厂商入口文件

Hook 组件除了 header / guard / 模型拉取 payload，还会收到最小重试上下文：

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
| `openai_images` | OpenAI Images 生成和编辑语义 |
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
| `/v1/images/generations` | `openai_images` |
| `/v1/images/edits` | `openai_images` |

`GET /v1/models` 除了模型 id，还会按模型类型返回：

- `provider_name`
- `source_format`
- `target_formats`
- `capabilities`

当 `api_keys.enabled=true` 时，`GET /v1/models` 与模型请求接口一样必须携带有效 API Key。返回模型列表会按以下顺序收窄：

1. 当前运行时可用模型
2. 如果启用 Chat 白名单，取当前客户端 IP 对应用户可访问模型
3. 如果启用 API Key 管理，再取当前 key 可访问模型

因此同时启用用户模型权限和 API Key 模型权限时，下游最终看到和能请求的模型是两者交集。

OAuth 模型是数据平面的例外路由：

- Provider 配置模型仍使用 `{provider}/{model}` key
- Codex / Claude OAuth 模型使用原始模型名，例如 `gpt-5-codex`、`claude-sonnet-4-5`
- Codex OAuth 图片模型使用原始模型名，例如 `gpt-image-2`
- 用户模型权限和 API Key 模型权限的可选目录同时包含 Provider 模型和当前可用 OAuth 模型
- 权限字段保存显式列表时，Provider 模型保存 `{provider}/{model}`，OAuth 模型保存原始模型名
- `ProxyController` 先查 Provider 映射，未命中时再查 Codex OAuth 模型目录，最后查 Claude OAuth 模型目录
- `/v1/models` 对 Codex OAuth 暴露普通模型名，`provider_name` 固定为 `codex`
- `/v1/models` 对 Codex OAuth 图片模型暴露 `target_formats=["openai_images"]` 与 `capabilities=["image_generation"]`
- `/v1/models` 对 Claude OAuth 暴露普通模型名，`provider_name` 固定为 `claude`
- Codex OAuth 代理复用 translator registry，把下游 `openai_chat` / `openai_responses` / `claude_chat` 转成 Codex backend 的 Responses 请求
- Codex OAuth 图片代理把 `/v1/images/generations` 和 `/v1/images/edits` 包装成 Codex backend Responses `image_generation` 工具调用
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
  - 保存后立即影响数据平面 `/v1/chat/completions`、`/v1/responses`、`/v1/messages`、`/v1/images/generations`、`/v1/images/edits` 和 `/v1/models` 是否要求下游携带 API Key
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
  - 数据面转发始终从发往上游的 header 中移除下游 `Authorization`
  - 统计完成回调写日志时会带上 `api_key_id`，用于同步累加 key 级用量
  - 流式 transport 失败和客户端取消不调用统计完成回调
- `ModelCatalogService`
  - 每次用户 / API Key 权限管理读取当前 Provider 配置模型和 Codex / Claude OAuth 可用模型
  - Codex OAuth 图片模型和普通 OAuth 模型使用同一显式权限列表语义
  - 不缓存模型目录，OAuth 模型变化后管理端重新加载即可出现在权限选择列表
- `CodexOAuthService`
  - 每次 token / quota / models 请求读取当前 `oauth.proxy_mode`、`oauth.proxy` 与 `oauth.verify_ssl`
  - 维护 OAuth PKCE 临时会话、Codex 账号配额冷却状态、认证文件配额刷新锁与 Codex 配额后台刷新 greenlet
  - 在 `data/oauth/codex/.state/auth_files.json` 持久化认证文件人工禁用状态、配额、最近一次模型代理状态与最近成功认证文件
  - 在 `data/oauth/codex/models.json`、`data/oauth/codex/image_models.json` 和 `data/oauth/codex/image_settings.json` 持久化本地文本模型目录、图片模型目录和默认图片模型
- `ClaudeOAuthService`
  - 每次 token / models 请求读取当前 `oauth.proxy_mode`、`oauth.proxy` 与 `oauth.verify_ssl`
  - 维护 OAuth PKCE 临时会话
  - 认证文件保存在 `data/oauth/claude/`
  - 在 `data/oauth/claude/.state/auth_files.json` 持久化认证文件人工禁用状态、最近一次模型代理状态与最近成功认证文件
- `CodexProxyService`
  - 每次 Codex 数据面请求读取当前 `oauth.proxy_mode`、`oauth.proxy` 与 `oauth.verify_ssl`
  - 普通 Codex 数据面请求按模型、账号计划、responses-lite 标记和默认图片模型决定是否携带 `image_generation` 工具
  - OpenAI Images 数据面请求固定使用 Codex OAuth 文本模型作为主请求模型，并用图片模型作为 `image_generation` 工具模型
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
- `max_retries`
  - 表示一次 Provider 上游操作允许的最大尝试次数，包含首次尝试
  - 默认值为 `3`
  - 值为 `1` 时只执行一次上游尝试
  - 流式 transport 异常只在下游响应提交前进入下一次尝试
- `hook`
  - 路径固定相对项目根目录 `hooks/`
  - 管理 API 输出和配置写入只保留本机存在且位于 `hooks/` 下的 hook 文件路径

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

#### Stream Commit And Completion

首个非空、已经完成目标协议编码的下游字节定义流提交边界。上游 HTTP 状态和原始上游 chunk 不定义该边界。

普通 Provider 提交前的 `RequestException` 和 `OSError` 会返回 `max_retries` 控制的 Provider 尝试循环。Codex / Claude OAuth 的同类异常会返回认证文件候选循环。提交后的 transport 异常通过当前下游协议结束流：

| downstream protocol | 流内错误输出 |
| --- | --- |
| `openai_chat` | OpenAI 兼容 error JSON data block，随后发送 `[DONE]` |
| `openai_responses` | `response.failed` |
| `claude_chat` | `event: error` |

目标协议终止事件已经发出时，后续 HTTP framing 异常保持逻辑完成状态。客户端取消会关闭上游响应并释放 Auth Group inflight，不调用成功完成回调，不写入成功请求统计，也不更新 Auth Entry 成功或失败状态。

Codex / Claude OAuth 流分别以 `response.completed` / `response.done` 和 `message_stop` 作为成功终止事件。缺少这些事件的 EOF 按未完成流处理：提交前进入下一认证文件候选，提交后发送下游协议错误事件并记录当前认证文件失败。普通 Provider 保持现有协议兼容收尾行为。

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
  -> fetch_models hook
  -> hook model result or model endpoint inference
  -> optional upstream fetch (/v1/models or /models)
  -> fetched model picker
  -> provider form model table
```

行为约束：

- 这两条都是控制平面能力，不经过下游 `/v1/chat/completions` / `/v1/responses` / `/v1/messages` / `/v1/images/*`
- 测试模型列表中的模型值按真实上游模型 ID 处理，不执行 `{provider_name}/` 路由前缀裁剪
- 两条链路都会使用 Provider 表单快照中的 `proxy_mode`、`proxy` 和 `verify_ssl`
- Provider 编辑页的 `model_list` 采用表格编辑，并以当前前端行状态作为唯一数据源
- 拉取模型只应用 `fetch_models`；Hook 返回 `None` 时使用内置端点探测
- 测试模型只应用 request-side hook：
  - `header_hook`
  - `request_guard`
- 两条链路都不应用 `response_guard`
- `auth_group` 模式下：
  - 拉取模型必须显式选择 `auth_entry`
  - 测试模型也必须显式选择 `auth_entry`
  - 两者都不经过 `AuthGroupManager.acquire()`
  - 两者都不写运行态冷却、并发、配额
- 首字延迟仅在真实流式首个正文或推理增量到达时记录
- TPS 仅在拿到 completion usage 后计算，计算口径为 `completion_tokens / 本次流式请求端到端耗时`
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
  - `POST /api/oauth/codex/models/restore-builtins`
  - `DELETE /api/oauth/codex/models/<model_id>`
  - `GET /api/oauth/codex/image-models`
  - `POST /api/oauth/codex/image-models`
  - `POST /api/oauth/codex/image-models/restore-builtins`
  - `PUT /api/oauth/codex/image-models/default`
  - `DELETE /api/oauth/codex/image-models/<model_id>`
  - `GET /api/oauth/codex/auth-files`
  - `POST /api/oauth/codex/auth-files/export`
  - `POST /api/oauth/codex/auth-files/import`
  - `POST /api/oauth/codex/auth-files/<name>/disable`
  - `POST /api/oauth/codex/auth-files/<name>/enable`
  - `DELETE /api/oauth/codex/auth-files/<name>`
  - `GET /api/oauth/codex/auth-files/<name>/quota`
  - `POST /api/oauth/codex/auth-files/<name>/reset-quota`
  - `POST /api/oauth/claude/session`
  - `POST /api/oauth/claude/callback`
  - `GET /api/oauth/claude/models`
  - `POST /api/oauth/claude/models`
  - `DELETE /api/oauth/claude/models/<model_id>`
  - `GET /api/oauth/claude/auth-files`
  - `POST /api/oauth/claude/auth-files/export`
  - `POST /api/oauth/claude/auth-files/import`
  - `POST /api/oauth/claude/auth-files/<name>/disable`
  - `POST /api/oauth/claude/auth-files/<name>/enable`
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
  -> list / manage local Codex text model IDs
  -> list / manage local Codex image model IDs and default image model
  -> list auth file token/status/quota snapshot
  -> optional quota refresh with current access token to chatgpt.com/backend-api/wham/usage
  -> skip duplicate quota refresh when the same auth file is already refreshing
  -> persist quota snapshot or quota error
  -> optional reset local quota snapshot and cooldown state
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
- Codex 文本模型目录缓存在 `data/oauth/codex/models.json`，文件内容只保存模型 ID 字符串数组
- Codex 图片模型目录缓存在 `data/oauth/codex/image_models.json`，文件内容只保存模型 ID 字符串数组
- Codex 图片模型设置缓存在 `data/oauth/codex/image_settings.json`，当前保存默认图片模型 ID
- Claude 模型目录缓存在 `data/oauth/claude/models.json`，文件内容只保存模型 ID 字符串数组
- 认证文件的人工禁用状态、最近配额、配额错误、数据面使用状态和最近成功认证文件保存在 `data/oauth/codex/.state/auth_files.json`
- Claude 认证文件的人工禁用状态、最近一次数据面使用状态和最近成功认证文件保存在 `data/oauth/claude/.state/auth_files.json`
- 认证文件列表会把候选筛选结果和触发原因作为状态显示；最近一次数据面错误摘要单独作为信息显示
- OAuth 页面为启用的认证文件显示禁用按钮，为禁用的认证文件显示启用按钮；禁用文件块使用灰态显示，并可通过顶部“禁用”筛选快速定位
- OAuth 页面认证文件列表按名称排序、每页最多展示 50 个，支持多文件导入、全选后批量启用、批量禁用、批量刷新额度、ZIP 导出和批量归档删除
- OAuth 页面导入认证文件时支持选择多个 JSON 文件或多个 ZIP 包；ZIP 必须是导出 API 生成的根目录 JSON 文件结构；每个 JSON 都会校验 provider 类型、access token、email 和过期时间，合法才写入认证目录
- OAuth 页面导入完成后按导入结果 toast 提示成功数量和失败数量
- OAuth 页面导出选中认证文件时调用导出 API，单个文件也会以 ZIP 下载
- OAuth 页面删除认证文件前会用气泡确认，确认后调用删除 API 把文件移动到 `deleted/`
- 同名 OAuth 登录和认证文件导入会更新认证内容并保留人工禁用状态；只有显式启用或删除文件会清除该禁用约束
- Codex 文本模型 ID 由用户在 OAuth 页面维护，初始目录包含项目内置常用 Codex 文本模型 ID
- Codex 图片模型 ID 由用户在 OAuth 页面维护，初始目录包含项目内置常用 Codex 图片模型 ID
- Codex 图片默认模型由用户在 OAuth 页面对应图片模型项中设置；当前默认值优先使用内置默认图片模型，目录中不存在时使用图片模型目录第一项
- Codex 文本模型和图片模型都支持添加内置模型；添加操作只追加缺失的项目内置模型 ID，不删除用户自行添加的模型 ID
- Claude 模型 ID 由用户在 OAuth 页面手动维护，默认列表为空
- OAuth 页面提供 `router-for-me/models` 仓库的 `models.json` 与 `https://models.router-for.me/models.json` 作为外部参考链接，不自动拉取
- Codex 查询配额时直接使用认证文件当前的 access token，不执行 token 刷新或认证失败后的刷新重试
- Codex 配额查询的代理优先级为认证文件 `proxy_url`、全局 OAuth 代理设置、直连
- Codex 数据面请求遇到 401 或认证类错误时，如果认证文件存在 refresh token，会先刷新认证文件并使用当前认证文件重试一次
- Claude OAuth 数据面请求前如果认证文件 access token 已过期，且存在 refresh token，会先刷新认证文件
- Codex / Claude 候选列表仍会按请求重建；人工禁用的认证文件不会进入候选列表；其余文件默认按认证文件修改时间倒序排列，最近一次真实请求成功的认证文件如果未被过滤，会被提升为第一候选
- 禁用状态作用于后续候选列表构建；已经选中或已经发往上游的请求继续完成
- 同一个认证文件的配额刷新使用进程内非阻塞锁；重复刷新请求会直接返回跳过结果，不重复访问 Codex 上游
- Codex 认证文件的 `reset-quota` 会清除本地配额快照、配额错误和进程内额度冷却；该操作不重置 OpenAI / ChatGPT 上游真实额度，也不清除认证失败状态
- 如果认证文件 access token 已过期且缺少 refresh token，请求候选筛选不会直接跳过；系统会先用当前 access token 尝试请求一次，再按上游返回的认证、配额或其他错误决定后续状态
- Codex 配额后台刷新任务随应用启动，第一轮在启动后 5 小时触发；每轮刷新所有未标记为认证失败、类型合法且包含 access token 的 Codex 认证文件，每个文件之间间隔 10 秒
- 配额刷新会同步内存冷却状态：Codex 窗口耗尽时冷却该认证文件，恢复可用时立即清除冷却
- Codex 数据面请求成功后，如果本地配额快照中的 Codex 窗口重置时间已经到期，会最佳努力刷新该认证文件的前端配额快照；刷新失败不会阻断本次模型响应
- Codex 数据面请求收到上游额度耗尽响应后，会立即真实刷新该认证文件的配额快照；刷新结果写入 OAuth 页面展示数据，刷新失败写入配额错误且不阻断候选账号切换
- 认证类错误会持久显示为认证失败并参与候选过滤；重新 OAuth 登录、token 刷新成功或后续真实请求成功后会清除该状态
- OAuth 顶层导航项是否显示由系统设置中的 `oauth.enabled` 控制
- token 交换、token 刷新与 OAuth 数据面代理使用系统设置中的 `oauth.proxy_mode`、`oauth.proxy` 和 `oauth.verify_ssl`；Codex 配额查询在认证文件没有 `proxy_url` 时使用该网络设置
- Codex 数据面请求在下游流提交前遇到上游错误或请求失败时，会记录当前认证文件信息并尝试下一个候选认证文件，直到成功或候选耗尽
- Codex 数据面请求在下游流提交后遇到 transport 失败时，会记录当前认证文件失败并发送下游协议错误事件，当前流不切换认证文件
- Codex 数据面请求被客户端取消时会关闭上游响应，不更新当前认证文件成功或失败状态
- Claude OAuth 数据面请求会在转发 Anthropic Messages 前按 CPA 请求方式重签已有 Claude Code billing header 的 `cch`
- 普通 Provider 如果 `source_format=claude_chat`，也会在上游 body 已有 Claude Code billing header 时重签 `cch`；不会主动生成 billing header
- Claude 数据面请求在下游流提交前遇到上游错误或请求失败时，会记录当前认证文件信息并尝试下一个候选认证文件，直到成功或候选耗尽
- Claude 数据面请求在下游流提交后遇到 transport 失败时，会记录当前认证文件失败并发送下游协议错误事件，当前流不切换认证文件
- Claude 数据面请求被客户端取消时会关闭上游响应，不更新当前认证文件成功或失败状态
- 出站 HTTP 请求遇到代理风险确认页时，会自动确认一次并重试原请求；自动确认失败或重试后仍被拦截时，返回 `proxy_warning_required` 和确认页 URL
- Codex 数据面请求在刷新重试后仍遇到 401 或认证类错误时，会将当前认证文件标记为认证失败，后续请求优先跳过
- Claude 上游返回 401 或认证类错误时，会将当前认证文件标记为认证失败，后续请求优先跳过
- OAuth 登录、文件、配额、模型目录与默认图片模型管理属于控制平面
- Codex / Claude 模型代理属于 `/v1/*` 数据平面，但不进入 Provider 路由或 Auth Group 选择流程
- Codex 图片生成和图片编辑接口属于 `/v1/*` 数据平面，使用 Codex OAuth 认证文件和图片模型目录，不进入 Provider 路由或 Auth Group 选择流程

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
  -> statistics completion callback
  -> request log insert
  -> API Key usage counter update
```

行为约束：

- `api_keys.enabled=false`
  - 数据面不要求下游 key
  - API Key 管理页签不显示
- `api_keys.enabled=true`
  - `/v1/chat/completions`、`/v1/responses`、`/v1/messages`、`/v1/images/generations`、`/v1/images/edits` 和 `/v1/models` 必须携带有效且启用的 key
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
- 统计完成回调执行后：
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
- [src/services/proxy_response_builder.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/proxy_response_builder.py)
  - 首块预取、流提交边界、协议错误事件和流终态结算
- [src/services/settings_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/settings_service.py)
  - 系统设置保存与生效边界
- [src/services/provider_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/provider_service.py)
  - Provider 配置管理、复制、排序、迁移 JSON 导入导出和重载触发
- [src/repositories/auth_group_repository.py](/root/.ww/code/002llm/000LLM_Proxy/src/repositories/auth_group_repository.py)
  - Auth Entry 运行态、用量桶、迁移表项导入导出和 Auth Group 运行态查询
- [src/services/user_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/user_service.py)
  - 用户管理、模型权限、IP 缓存、迁移 JSON 导入导出和批量删除
- [src/services/log_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/log_service.py)
  - 请求日志查询、统计聚合、Excel 数据读取和统计迁移 JSON 导入导出
- [src/repositories/log_repository.py](/root/.ww/code/002llm/000LLM_Proxy/src/repositories/log_repository.py)
  - 请求日志、日聚合统计、筛选查询、请求明细去重导入和日聚合统计合并导入
- [src/services/api_key_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/api_key_service.py)
  - API Key 生成、hash 鉴权、模型权限、token 上限和 key 级用量管理
- [src/services/model_catalog_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/model_catalog_service.py)
  - 汇总 Provider 配置模型、Codex / Claude OAuth 可用模型与 Codex 图片模型，供用户和 API Key 模型权限共用
- [src/repositories/api_key_repository.py](/root/.ww/code/002llm/000LLM_Proxy/src/repositories/api_key_repository.py)
  - API Key 持久化、列表排序和累计用量字段
- [src/services/codex_oauth_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/codex_oauth_service.py)
  - Codex OAuth PKCE、token 文件、本地文本模型目录、图片模型目录、默认图片模型与配额查询
- [src/services/claude_oauth_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/claude_oauth_service.py)
  - Claude OAuth PKCE、token 文件、本地模型 ID 目录与认证文件管理
- [src/services/oauth_auth_file_archive.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/oauth_auth_file_archive.py)
  - OAuth 认证文件 ZIP 导出、JSON/ZIP 导入展开与删除归档移动
- [src/services/codex_proxy_service.py](/root/.ww/code/002llm/000LLM_Proxy/src/services/codex_proxy_service.py)
  - Codex OAuth 数据面代理、账号配额切换、`image_generation` 工具补齐与 OpenAI Images 兼容桥接
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
  - OAuth 管理页面、Codex 文本模型与图片模型设置、Claude 子 tab

## 5. Physical View

部署上是单体服务：

- 一个 Flask 应用
- 一个配置文件
- 一个 SQLite 数据库，保存用户、请求日志、日聚合统计与 API Key
- 一组滚动日志文件
- 一个 Codex 配额后台刷新 greenlet
- 一组本地 OAuth 认证文件
- 一组本地 OAuth 模型目录缓存
- 一组本地 Codex 图片模型默认设置
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
    Service->>Translator: openai_chat -> openai_responses
    Translator-->>Service: translated upstream request
    Service->>Executor: execute HTTP request
    Executor-->>Service: HTTP response + lazy stream
    Service->>Translator: translate first stream event
    Translator-->>Service: first openai_chat chunk
    Service->>Service: 编码并预取首个非空下游字节
    alt 提交前 transport 失败且仍有尝试次数
        Service->>Executor: 关闭当前上游响应
        Service->>Service: 下一次尝试重新进入请求、转换、编码与首块预取
    else 首个下游字节就绪
        Service-->>Controller: SSE response + prefetched bytes
        Controller-->>Client: first chat.completion.chunk
        Note over Controller,Client: 首个下游字节发送后流已提交
        loop remaining stream events
            Executor-->>Service: stream event
            Service->>Translator: translate stream event
            Translator-->>Service: openai_chat chunk
            Service-->>Controller: encoded SSE chunk
            Controller-->>Client: chat.completion.chunk
        end
        alt 目标协议终止事件完成
            Service->>Service: 成功完成回调与统计
        else 提交后 transport 或流处理失败
            Service-->>Controller: OpenAI error data + [DONE]
            Controller-->>Client: protocol error in stream
        else 客户端取消
            Client-->>Controller: close downstream connection
            Controller-->>Service: close response iterator
            Service->>Executor: close upstream response
        end
    end
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
    CodexOAuth->>CodexOAuth: 过滤人工禁用/认证失败/冷却文件，并优先最近成功认证文件
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
        CodexProxy->>CodexOAuth: refresh_auth_file_quota_snapshot()
        CodexProxy->>ChatGPT: 使用下一个认证文件重试
    else 账号认证失败
        ChatGPT-->>CodexProxy: 401 authentication_error
        opt 当前认证文件存在 refresh_token
            CodexProxy->>CodexOAuth: refresh_auth_candidate()
            CodexProxy->>ChatGPT: 使用当前认证文件重试一次
        end
        alt 刷新不可用或重试后仍认证失败
            CodexProxy->>CodexOAuth: record_auth_file_failure()
            CodexProxy->>ChatGPT: 使用下一个认证文件重试
        end
    end
    ChatGPT-->>CodexProxy: Responses SSE lazy stream
    CodexProxy->>CodexProxy: 翻译、编码并预取首个非空下游字节
    alt 提交前 transport 失败
        CodexProxy->>CodexOAuth: record_auth_file_failure()
        CodexProxy->>CodexProxy: 下一候选认证文件重新进入请求、转换、编码与首块预取
    else 下游流已提交
        CodexProxy-->>Controller: 下游协议响应 + prefetched bytes
        Controller-->>Client: first stream chunk
        alt response.completed / response.done
            CodexProxy->>CodexOAuth: record_auth_file_success()
            CodexOAuth->>CodexOAuth: 记录最近成功认证文件
            opt 本地配额快照 reset_at 已到期
                CodexOAuth->>ChatGPT: GET /backend-api/wham/usage
                CodexOAuth->>CodexOAuth: 更新认证文件配额快照
            end
        else 提交后 transport 失败
            CodexProxy->>CodexOAuth: record_auth_file_failure()
            CodexProxy-->>Controller: target protocol error event
            Controller-->>Client: protocol error in stream
        else 客户端取消
            Controller->>CodexProxy: close response iterator
            CodexProxy->>ChatGPT: close upstream response
            Note over CodexProxy,CodexOAuth: 认证文件状态保持不变
        end
    end
    Note over CodexProxy,ChatGPT: response.completed 后的 HTTP framing error 保持逻辑完成状态
```

### 6.3 OpenAI Images Downstream -> Codex Image Generation Tool

```mermaid
sequenceDiagram
    participant Client
    participant Controller
    participant CodexOAuth
    participant CodexProxy
    participant ChatGPT

    Client->>Controller: POST /v1/images/generations
    Controller->>Controller: 读取 JSON 或 multipart 请求体
    Controller->>Controller: 缺少 model 时使用 Codex 默认图片模型
    Controller->>Controller: 校验白名单、API Key、图片模型权限和 token 上限
    Controller->>CodexOAuth: iter_auth_candidates_for_model(main_text_model)
    CodexOAuth->>CodexOAuth: 过滤 free plan、人工禁用、认证失败和冷却文件
    Controller->>CodexProxy: proxy_image_request(action=generate)
    CodexProxy->>CodexProxy: 构造 Responses 请求与 image_generation 工具配置
    CodexProxy->>ChatGPT: POST /backend-api/codex/responses
    ChatGPT-->>CodexProxy: image_generation_call result or stream events
    alt 非流式
        CodexProxy-->>Controller: OpenAI Images JSON data
        Controller-->>Client: b64_json or data URL
    else 流式
        CodexProxy-->>Controller: image_generation partial/completed SSE
        Controller-->>Client: OpenAI Images compatible SSE
    end
    CodexProxy->>CodexOAuth: record_auth_file_success()
```

`/v1/images/edits` 使用同一链路，并把上传文件或请求中的图片 URL 作为 `input_image` 内容传给 Codex `image_generation` 工具。

### 6.4 Claude Downstream -> OpenAI Chat Upstream

```mermaid
sequenceDiagram
    participant Client
    participant Controller
    participant Service
    participant Translator
    participant Executor

    Client->>Controller: POST /v1/messages
    Controller->>Service: proxy_request()
    Service->>Translator: claude_chat -> openai_chat
    Translator-->>Service: upstream chat request
    Service->>Executor: execute HTTP request
    Executor-->>Service: HTTP response + lazy chat SSE stream
    Service->>Translator: translate first stream event
    Translator-->>Service: first Claude event
    Service->>Service: 编码并预取首个非空下游字节
    alt 提交前 transport 失败且仍有尝试次数
        Service->>Executor: 关闭当前上游响应
        Service->>Service: 下一次尝试重新进入请求、转换、编码与首块预取
    else 下游流已提交
        Service-->>Client: first Claude-style SSE event
        alt message_stop 完成
            Service->>Service: 成功完成回调与统计
        else 提交后 transport 或流处理失败
            Service-->>Client: event: error
        else 客户端取消
            Client-->>Service: close downstream connection
            Service->>Executor: close upstream response
        end
    end
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

流提交边界由首个非空目标协议编码字节定义。提交后的错误输出格式由下游协议决定，当前流不执行透明重试。目标协议终止事件已经发出时，后续 HTTP framing 异常保持逻辑完成状态。
