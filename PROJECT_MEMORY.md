# 项目记忆（LLM_Proxy）

适用范围：`LLM_Proxy` 仓库根目录

## 基础约束
- 所有文本文件使用 `UTF-8`，无 BOM。
- 所有文本文件统一使用 `LF` (`\n`)，禁止 `CRLF` (`\r\n`)。
- 运行日志统一使用英文。
- 代码注释和 docstring 目标规范为中文。
- Python 字符串风格默认使用双引号 `"..."`。
- Python `docstring` 统一使用三重双引号 `"""..."""`。
- 只有在减少转义、提升可读性时，普通字符串才使用单引号 `'...'`。
- 涉及代码、配置、架构的修改时，优先保持控制平面与数据平面职责清晰。

## 项目定位
- 这是一个基于 Flask + gevent 的 OpenAI 兼容代理服务。
- 核心能力是将 `/v1/chat/completions` 请求路由到配置中的上游 Provider。
- 系统同时提供后台管理、认证、用户白名单、Provider 管理、模型探测和请求统计能力。

## 启动与装配链路
- 入口文件：`main.py`
- 启动关键点：
  - 使用 `gevent.monkey.patch_all()`
  - 支持 `--config` 指定配置文件，默认使用项目根目录 `config.yaml`
  - 调用 `Application.run()`，由 gevent `WSGIServer` 提供服务
- 组合根：`src/application/application.py`
- 当前装配顺序：
  - 初始化 `ConfigManager`
  - 初始化应用日志和访问日志
  - 创建 `AppContext`
  - 初始化 SQLite 连接工厂和仓储
  - 初始化 `ProviderManager` 并加载 Provider
  - 组装 Service 和 Controller 并注册路由

## 当前分层
- `src/application`
  - 组合根与运行时上下文
- `src/presentation`
  - HTTP 路由、鉴权入口、页面渲染、请求和响应适配
- `src/services`
  - 用例编排和业务逻辑
  - 当前已拆分为：
    - `AuthenticationService`
    - `UserService`
    - `ProviderService`
    - `ModelDiscoveryService`
    - `SettingsService`
    - `ProxyService`
    - `LogService`
- `src/repositories`
  - SQLite 数据访问
- `src/config`
  - 配置快照管理、显式 Provider schema / factory、Provider 运行时注册
- `src/external`
  - 上游响应适配、流式探测、Provider 运行时对象
- `src/hooks`
  - Hook 协议和动态扩展点
- `src/utils`
  - 数据库、网络、IP 处理等通用工具

## 架构关键点
- 当前是单进程分层单体。
- 控制平面：
  - 认证
  - 用户管理
  - Provider 配置管理
  - 系统设置管理
- 数据平面：
  - `/v1/chat/completions`
  - `/v1/models`
  - 模型路由
  - 上游转发
  - 响应适配
  - 请求日志记录

## 配置与 Provider 运行时
- `ConfigManager`
  - 提供配置快照读取
  - 提供原子写回
  - `get_raw_config()` 返回 `deepcopy`，避免调用方误改内部缓存
- `provider_config`
  - 负责 `ProviderConfigSchema`、`RuntimeProviderSpec`、`ProviderRuntimeView`
  - 负责批量 `build_provider_schemas(...)`、配置规范化和校验
- `ProviderManager`
  - 只接收已构建好的 `ProviderConfigSchema`
  - 负责把 schema 转成运行时 `LLMProvider`
  - 维护 `model -> provider` 映射、只读 `ProviderRuntimeView` 注册表和 Hook 缓存
  - 对外优先暴露只读接口：`get_provider_view()`、`list_provider_views()`、`list_model_names()`
- `LLMProvider`
  - 运行时 Provider 对象
  - 当前会复制 `model_list`，避免共享可变引用

## 核心请求流程（chat/completions）
1. `ProxyController.chat_completions` 接收 `POST /v1/chat/completions`
2. 标准化客户端 IP
3. 读取 `chat.whitelist_enabled`
4. 如果开启白名单，则按 IP 查询用户，并要求 `whitelist_access_enabled=1`
5. 校验请求体必须包含 `model`
6. 补齐 `stream_options.include_usage=true`
7. 用 `ProviderManager.get_provider_for_model(model)` 获取目标 Provider
8. `ProxyService.proxy_request(...)` 转发到上游
9. `response_adapter` 处理普通响应或 SSE 响应
10. 响应完成后通过回调写入 `request_logs` 并同步更新 `daily_request_stats`

## 模型路由规则
- 配置中每个 Provider 至少声明：
  - `name`
  - `api`
  - `model_list`
- 系统内部模型 key 格式：
  - `{provider_name}/{model_name}`
- 代理接口请求体中的 `model` 必须使用该格式
- 转发给上游前，`ProxyService` 会把模型名裁剪成最后一段真实模型名

## 模型探测链路
- `ProviderController.fetch_models()`
- `ModelDiscoveryService.fetch_models_preview(...)`
- 自动尝试候选端点：
  - `/v1/models`
  - `/models`
- 支持从 `chat/completions` 风格 URL 反推模型列表端点

## Hook 机制
- 配置字段：`providers[].hook`
- 路径规则：
  - 绝对路径直接加载
  - 相对路径默认相对项目根目录 `hooks/`
- Hook 模块必须导出 `Hook` 类
- 可选方法：
  - `header_hook(ctx, headers) -> headers`
  - `input_body_hook(ctx, body) -> body`
  - `output_body_hook(ctx, body) -> body`
- `ProviderManager` 负责 Hook 动态加载和缓存

## 认证与会话
- 是否开启认证取决于 `admin.username` 和 `admin.password` 是否都存在
- `AuthenticationService` 当前使用进程内内存 Session
- 服务重启后 Session 全部失效
- 当前不适合多实例横向扩展

## 用户与白名单
- `UserService` 对按 IP 查询用户做了内存缓存
- 用户变更时会主动失效对应缓存
- 白名单控制依赖：
  - `chat.whitelist_enabled`
  - 用户 `whitelist_access_enabled`

## 日志与统计
- 访问日志写入 `logs/access.log`
- 应用日志写入 `logs/app.log`
- `LogRepository.insert()` 在一个事务内同时写：
  - `request_logs`
  - `daily_request_stats`

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
- Provider 管理：
  - `GET /api/providers`
  - `POST /api/providers`
  - `GET /api/providers/<name>`
  - `PUT /api/providers/<name>`
  - `DELETE /api/providers/<name>`
  - `GET /api/providers/fetch-models`
  - `PUT /api/providers/chat-whitelist`
- 页面：
  - `GET /`
  - `GET /users`
  - `GET /providers`

## 数据库结构
- 默认路径：`data/requests.db`
- `users`
  - `id`
  - `username`
  - `ip_address`
  - `whitelist_access_enabled`
  - `created_at`
  - `updated_at`
- `request_logs`
  - `ip_address`
  - `request_model`
  - `response_model`
  - `total_tokens`
  - `prompt_tokens`
  - `completion_tokens`
  - `start_time`
  - `end_time`
- `daily_request_stats`
  - 唯一键：`(stat_date, ip_address, request_model, response_model)`

## 当前风险与注意事项
- 历史文件里有较多中文乱码的注释和 docstring，修改相关文件时优先逐步修复，避免继续扩散。
- `output_body_hook` 在非流式分支如果返回 `None`，可能把响应体变成 `null`；新增 Hook 时要注意返回值约定。
- 认证 Session、用户 IP 缓存、Provider 映射和 Hook 缓存都在单进程内存中，不适合多实例部署。

## 4+1 架构文档维护
- 4+1 Mermaid 文档文件：`docs/architecture-4plus1.md`
- 涉及以下变更时，必须同步更新该文档：
  - 模块职责变化
  - 分层依赖变化
  - 主请求链路变化
  - Provider 重载链路变化
  - 运行时缓存或内存状态变化
  - Hook 机制变化
  - 部署拓扑变化
- 如果代码提交没有修改 4+1 图，变更说明中需要明确声明“架构未变化”。
