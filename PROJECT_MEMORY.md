# 项目记忆（ai_proxy）
适用范围：`d:\001Code\008llm\003ai_proxy`

## 基础约束
- 所有文本文件使用 `UTF-8`（无 BOM）。
- 运行日志统一英文。
- 代码注释和 docstring 目标规范为中文。

## 项目定位
- 这是一个 Flask + gevent 的 OpenAI 兼容代理服务。
- 核心能力是将 `/v1/chat/completions` 请求路由到配置中的上游 Provider，并支持 Hook 扩展、白名单控制、后台管理和日志统计。

## 启动与装配链路
- 入口文件：`main.py`
- 启动关键点：
- 使用 `gevent.monkey.patch_all()`。
- 支持 `--config` 指定配置文件，默认使用项目根目录 `config.yaml`。
- 启动 `Application.run()`，由 gevent `WSGIServer` 提供服务。
- 应用装配：`src/application/application.py`
- 初始化顺序：
- 读取配置 `ConfigManager`
- 初始化日志（`app.log`、`access.log`）
- 注册 `before_request` 访问日志钩子（记录 `ip + url`）
- 创建 `AppContext`
- 初始化 SQLite 连接工厂与仓储
- 加载 Provider 与 Hook
- 组装 Service 与 Controller，并注册路由

## 分层职责
- `src/presentation`：HTTP 路由与输入输出校验。
- `src/services`：业务逻辑编排（认证、用户、代理、日志）。
- `src/repositories`：SQLite 数据访问。
- `src/config`：配置读取与 provider/hook 装载。
- `src/external`：上游 LLM provider 抽象与输出处理。
- `src/common`：上下文对象与类型协议。
- `src/utils`：数据库连接、IP 处理等通用工具。

## 核心请求流程（chat/completions）
1. `ProxyController.chat_completions` 接收 `POST /v1/chat/completions`。
2. 标准化客户端 IP（`normalize_ip`）。
3. 若 `chat.whitelist_enabled=true`，按 IP 查用户且要求 `whitelist_access_enabled=1`。
4. 校验请求体含 `model`。
5. 强制补齐 `stream_options.include_usage=true`。
6. 用 `ProviderManager.find_provider_by_model(model)` 找 provider。
7. 调用 `ProxyService.proxy_request(...)` 转发到上游。
8. 响应完成后通过回调写入 `request_logs` 并更新 `daily_request_stats`。

## 模型路由规则
- Provider 在配置中声明 `name` 与 `model_list`。
- 系统内部模型 key 格式是：`{provider_name}/{model_name}`。
- 代理接口请求体中的 `model` 必须匹配上述 key（例如 `volc/glm-4-7-251222`）。
- 转发给上游前，`ProxyService` 会将模型名改为去前缀后的真实模型名（仅保留 `/` 后最后一段）。

## Hook 机制
- 在 `config.yaml` 的 provider 项可配置：
- `header_hook`
- `input_body_hook`
- `output_body_hook`
- Hook 路径规则：
- 绝对路径直接加载。
- 相对路径默认相对项目根的 `hooks/` 目录加载。
- `ProviderManager` 对 hook 模块做缓存，避免重复导入。
- Hook 函数约定：
- `header_hook(ctx, headers) -> headers`
- `input_body_hook(ctx, body) -> body`
- `output_body_hook(ctx, data) -> Optional[data]`
- `ctx` 常用字段：
- `retry`（当前重试序号，从 0 开始）
- `ip_address`
- `user_id`

## 流式与非流式输出处理
- 输出处理入口：`LLMProvider.apply_output_body_hook(...)`。
- 非流式：
- 读取完整 JSON，提取 `model` 与 `usage`（`total/prompt/completion_tokens`）。
- 若配置了输出 hook，则以 hook 返回内容作为响应体。
- 流式（SSE）：
- 按事件块解析 `data:` 行，逐块执行输出 hook（若存在）。
- 识别 `[DONE]`。
- 在流结束时触发 `on_complete`，用于日志落库与统计。

## 重试与网络行为
- `ProxyService` 使用 `requests.Session`。
- 每个 provider 可配置：
- `timeout_seconds`（默认 300）
- `max_retries`（默认 3）
- `verify_ssl`（默认 false）
- 仅对 `requests.exceptions.RequestException` 进行重试。

## 认证与权限
- 认证是否启用取决于 `admin.username` 和 `admin.password` 是否都存在。
- 启用后：
- 页面接口未登录重定向 `/login`。
- API 未登录返回 `401 {"error":"Unauthorized"}`。
- 会话存储在内存（`AuthenticationService._sessions`），服务重启后会话失效。
- cookie 默认：
- `httponly=true`
- `samesite=Lax`
- `secure=false`
- `max_age=86400`

## 主要路由
- 代理接口：
- `POST /v1/chat/completions`
- `GET /v1/models`
- 认证接口：
- `GET /login`
- `POST /api/login`
- `GET /logout`
- `POST /api/logout`
- 用户管理：
- `GET /api/users`
- `POST /api/users`
- `GET /api/users/<id>`
- `PUT /api/users/<id>`
- `DELETE /api/users/<id>`
- `POST /api/users/<id>/toggle`
- 统计与日志：
- `GET /api/statistics`
- `GET /api/request-logs`
- `GET /api/usernames`
- 页面：
- `GET /`
- `GET /users`

## 数据库结构（SQLite）
- 默认路径：`data/requests.db`（可由 `database.path` 覆盖）。
- 表：
- `users`
- 字段：`id`, `username`, `ip_address(unique)`, `whitelist_access_enabled`, `created_at`, `updated_at`
- `request_logs`
- 字段：`ip_address`, `request_model`, `response_model`, `total_tokens`, `prompt_tokens`, `completion_tokens`, `start_time`, `end_time`
- `daily_request_stats`
- 唯一键：`(stat_date, ip_address, request_model, response_model)`
- 用于按天聚合请求次数与 token 数据

## 关键实现细节
- `ProxyController` 会过滤 hop-by-hop 请求头后再转发。
- `ProxyService` 会过滤 hop-by-hop 响应头返回给客户端。
- `UserService` 对按 IP 查用户做了内存缓存，并在用户变更时失效缓存。
- `UserRepository.get()` 会关联 `daily_request_stats` 返回用户累计请求/Token统计。
- `LogRepository.insert()` 在单事务内同时写明细日志与日聚合统计。

## 当前开发注意事项
- 代码中存在较多中文注释/docstring 乱码现象，修改相关文件时优先一并修复，避免继续扩散。
- `output_body_hook` 在非流式分支若返回 `None`，当前会把响应体变成 `null`；新增 hook 时应明确返回原对象或改造此行为。
- 会话是进程内存态，不适合多实例部署；如果未来需要横向扩展，应改为共享会话存储。
