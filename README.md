# LLM_Proxy

> 一个面向多上游、多协议场景的统一 LLM 接入层。

LLM_Proxy 用来把不同的模型服务、鉴权方式和传输协议收敛到同一个服务里，对下游稳定提供 OpenAI Chat Completions、OpenAI Responses 和 Claude Messages 兼容接口，并内置 Provider 管理、认证池调度、白名单控制、日志统计和 Hook 扩展能力。

它的定位不是“再做一个 API 转发器”，而是把代理、协议适配、认证管理和运维入口放到同一层里统一处理。对客户端来说，只需要记住一个地址；对服务端来说，模型路由、Key 池、访问控制和统计信息都可以集中维护。

## 项目亮点

- 统一接入多种上游：同一个服务可以同时接 OpenAI Chat、OpenAI Responses、Claude Messages、Codex 风格上游
- 对下游保持稳定接口：支持 `POST /v1/chat/completions`、`POST /v1/responses`、`POST /v1/messages`、`GET /v1/models`
- 协议转换明确可控：通过 `source_format` 和 `target_format` 驱动内置 translator 做协议转换
- 多 Provider 精确路由：下游使用 `provider/model` 即可命中指定上游
- 多 Key 池化调度：`auth_group` 支持并发限制、429 冷却、分钟/日请求配额、分钟/日 Token 配额
- WebSocket 上游支持：上游可以是 HTTP，也可以是 WebSocket；下游接口保持不变
- Hook 扩展机制：用于补充 Header、请求护栏和成功响应清洗
- 后台可直接运维：内置 Provider 管理、Auth Group 管理、白名单管理、日志和统计面板

## 3 分钟快速开始

### 1. 安装依赖

```bash
pip install flask gevent requests pyyaml urllib3 websocket-client
```

### 2. 准备配置文件

创建 `config.yaml`，下面是一份最小可运行配置：

```yaml
server:
  host: 127.0.0.1
  port: 8080

# chat:
#   whitelist_enabled: false

# admin:
#   username: admin
#   password: admin123

providers:
  - name: openai-chat
    # enabled: false  # 可选；默认 true；禁用后不会出现在 /v1/models
    api: https://api.openai.com/v1/chat/completions
    transport: http
    source_format: openai_chat
    target_format: openai_chat
    api_key: sk-your-openai-key
    verify_ssl: false
    model_list:
      - gpt-4.1
      - gpt-4.1-mini
```

也可以直接从 [config.sample.yaml](config.sample.yaml) 开始裁剪。
注意：下游调用时的模型名不是裸模型名，而是 `provider/model`，例如 `openai-chat/gpt-4.1`。
如果未配置 `providers[].enabled`，默认就是启用状态。

### 3. 启动服务

```bash
python main.py
```

或者显式指定配置文件：

```bash
python main.py --config path/to/config.yaml
```

### 4. 验证服务是否可用

查询模型列表：

```bash
curl http://127.0.0.1:8080/v1/models
```

发送第一条聊天请求：

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai-chat/gpt-4.1",
    "messages": [
      {"role": "user", "content": "你好，帮我用一句话介绍 LLM_Proxy"}
    ],
    "stream": false
  }'
```

后台页面始终可访问；如果配置了 `admin.username` 和 `admin.password`，访问后台时需要先登录；不配置则后台无需登录：

- `/login`：登录页
- `/`：统计与日志面板
- `/users`：用户与白名单管理
- `/providers`：Provider / Auth Group 管理

## 使用场景

### 1. 局域网里的统一 LLM 入口

把服务部署在 NAS、小主机或团队内网服务器上，让桌面客户端、脚本、自动化任务统一走一个地址访问模型。客户端不需要再分别维护不同厂商的 API 地址和鉴权方式。

### 2. 多上游收敛到一层

同时接入 OpenAI、Anthropic、私有网关或自建兼容层，对外仍然提供统一接口。业务侧只关心模型名，不需要感知每个上游的差异。

### 3. 多 Key 池化和限流

当一个上游需要多个 Key 分摊压力时，可以把多个 Header 凭据放进 `auth_group`，由代理统一处理并发、配额、冷却和禁用状态。

### 4. 非标准上游协议适配

当上游不是标准 OpenAI 格式时，优先通过 `source_format` 和 `target_format` 使用内置协议转换；如果还需要补充 Cookie、自定义 Header、特殊字段，或者对成功响应做额外清洗，再用 Hook 在代理层补充这部分逻辑。

### 5. 协议迁移期兼容

上游已经切到 Responses，但客户端还在走 Chat Completions；或者一部分客户端走 Claude Messages，另一部分走 OpenAI 风格接口。这类迁移期混合场景正适合由代理层统一兜住。

## 接口概览

| 路由 | 说明 | 允许的 `target_format` |
| --- | --- | --- |
| `POST /v1/chat/completions` | OpenAI Chat Completions 兼容接口 | `openai_chat` |
| `POST /v1/responses` | OpenAI Responses / Codex 风格接口 | `openai_responses`、`codex` |
| `POST /v1/messages` | Claude Messages 兼容接口 | `claude_chat` |
| `GET /v1/models` | 返回当前已注册模型列表 | 不适用 |

返回的模型 ID 形如：

- `openai-chat/gpt-4.1`
- `responses-upstream/gpt-4.1`
- `claude-messages/claude-sonnet-4-5`

## 配置介绍

### 顶层配置

- `server.host`：监听地址，默认 `127.0.0.1`
- `server.port`：监听端口，默认 `8080`
- `chat.whitelist_enabled`：是否启用聊天白名单
- `admin.username` / `admin.password`：配置后开启后台登录功能，用于提升后台访问安全性；不配置则后台无需登录
- `auth_groups`：凭据池配置
- `providers`：上游模型入口配置
- `database.path`：SQLite 数据库路径，默认 `data/requests.db`
- `logging.path`：日志目录，默认 `logs`
- `logging.level`：日志级别，例如 `INFO`、`DEBUG`

### `providers[]` 字段说明

- `name`：Provider 名称，必须唯一；下游模型名会以它作为前缀
- `enabled`：是否启用；默认 `true`。设为 `false` 后该 Provider 不参与运行时注册，也不会出现在 `GET /v1/models`
- `api`：上游接口地址，支持 `http://`、`https://`、`ws://`、`wss://`
- `transport`：上游传输方式，支持 `http` 和 `websocket`
- `source_format`：上游真实协议格式
- `target_format`：当前 Provider 对下游暴露的协议格式
- `api_key`：单 Provider 直接使用的凭据
- `auth_group`：绑定一个凭据池；和 `api_key` 二选一，不能同时使用
- `proxy`：上游代理地址
- `timeout_seconds`：上游请求超时，默认 `1200`
- `max_retries`：失败重试次数，默认 `3`
- `verify_ssl`：是否校验证书；代码默认值为 `false`，公网 HTTPS 建议显式设为 `true`
- `model_list`：当前 Provider 暴露的模型列表
- `hook`：Hook 文件路径；支持绝对路径。相对路径会从项目根目录下的 `hooks/` 目录加载，文件中需要导出名为 `Hook` 的类

支持的协议格式：

- `openai_chat`
- `openai_responses`
- `claude_chat`
- `codex`

如何理解 `source_format` 和 `target_format`：

- `source_format` 描述上游实际上接受什么协议
- `target_format` 描述这个 Provider 希望下游通过什么协议访问

例如：

- 上游和下游都用 Chat Completions：`openai_chat -> openai_chat`
- 上游是 Responses，下游继续暴露 Chat Completions：`openai_responses -> openai_chat`
- 上游和下游都走 Claude Messages：`claude_chat -> claude_chat`

### `auth_groups[]` 字段说明

- `name`：认证组名称，必须唯一
- `strategy`：当前支持 `least_inflight`
- `cooldown_seconds_on_429`：组级默认冷却时间
- `entries[]`：凭据条目列表，至少包含一个条目

`entries[]` 支持：

- `id`：条目标识
- `enabled`：是否启用
- `headers`：发往上游时注入的 Header 集合，例如 `Authorization`
- `max_concurrency`：单条目最大并发
- `cooldown_seconds_on_429`：条目级 429 冷却时间
- `request_quota_per_minute` / `request_quota_per_day`：请求数配额
- `token_quota_per_minute` / `token_quota_per_day`：Token 配额

运行时行为：

- 遇到 `429` 时，会优先参考 `Retry-After`，只让当前条目进入冷却
- 遇到 `401` 时，当前条目会被标记为不可用，直到后台手动恢复
- 后台支持清除冷却、启用、禁用、恢复、重置分钟用量、重置运行时状态

## 常见配置示例

### 1. 标准 OpenAI Chat 上游

```yaml
providers:
  - name: openai-chat
    api: https://api.openai.com/v1/chat/completions
    source_format: openai_chat
    target_format: openai_chat
    api_key: ${OPENAI_API_KEY}
    verify_ssl: true
    model_list:
      - gpt-4.1
      - gpt-4.1-mini
```

### 2. 上游是 Responses，下游暴露 Chat Completions

```yaml
providers:
  - name: responses-upstream
    api: https://api.openai.com/v1/responses
    source_format: openai_responses
    target_format: openai_chat
    api_key: ${OPENAI_API_KEY}
    verify_ssl: true
    model_list:
      - gpt-4.1
```

### 3. 使用 Auth Group 管理多个 Key

```yaml
auth_groups:
  - name: openai-shared
    strategy: least_inflight
    cooldown_seconds_on_429: 60
    entries:
      - id: key-a
        headers:
          Authorization: Bearer sk-key-a
        max_concurrency: 3
        request_quota_per_minute: 60
      - id: key-b
        headers:
          Authorization: Bearer sk-key-b
        max_concurrency: 2

providers:
  - name: openai-chat
    api: https://api.openai.com/v1/chat/completions
    source_format: openai_chat
    target_format: openai_chat
    auth_group: openai-shared
    verify_ssl: true
    model_list:
      - gpt-4.1
```

### 4. 使用 Hook 做协议补充

```yaml
providers:
  - name: custom-gateway
    api: https://example.com/v1/chat/completions
    source_format: openai_chat
    target_format: openai_chat
    model_list:
      - my-model
    hook: example_hook.py
```

完整样例见：

- [config.sample.yaml](config.sample.yaml)
- [hooks/example_hook.py](hooks/example_hook.py)

## 功能介绍

### 1. 代理与协议转换

- 按请求里的 `model` 自动选择对应 Provider
- 按 `target_format` 校验当前路由是否匹配，避免请求打错入口
- 支持流式和非流式响应
- 自动识别 SSE、NDJSON、WebSocket JSON 等上游返回形态
- `GET /v1/models` 会返回当前已启用 Provider 的模型列表，以及 `provider_name`、`source_format`、`target_format`、`transport` 等元信息

### 2. Provider 与 Auth Group 管理

- Provider 增删改查
- 支持行内启用 / 禁用，以及批量启用 / 禁用 / 删除
- 拉取上游模型列表并辅助填充 `model_list`
- Auth Group 增删改查
- YAML 批量导入 Auth Entries
- 查看 Auth Group 运行时状态
- 对单个条目执行清冷却、禁用、启用、恢复、重置等运维动作

### 3. 后台与访问控制

- 登录页：`/login`
- 统计与日志面板：`/`
- 用户管理：`/users`
- Provider / Auth Group 管理：`/providers`
- 白名单按 IP 控制
- 当 `chat.whitelist_enabled=true` 时，只有已登记且启用白名单权限的用户 IP 才能访问代理接口

### 4. 日志与统计

- 应用日志写入 `logs/app.log`
- 访问日志写入 `logs/access.log`
- 请求明细和每日聚合统计写入 SQLite
- 后台支持按日期、用户名、模型过滤统计和日志数据

### 5. Hook 扩展

Hook 文件需要导出一个名为 `Hook` 的类。通常继承 `BaseHook`，也可以直接实现同名方法：

```python
from src.hooks import BaseHook, HookContext
```

可选扩展点有 3 个：

- `header_hook(ctx, headers) -> headers`
- `request_guard(ctx, body) -> body`
- `response_guard(ctx, body) -> body`

职责边界先说明白：

- 标准协议转换由 `source_format`、`target_format` 对应的 translator 完成
- Hook 不是协议转换层，不负责定义 `openai_chat`、`openai_responses`、`claude_chat`、`codex` 之间的标准映射
- Hook 负责的是请求进入上游前后的定制化处理，例如补 Header、做请求护栏、调整局部字段、清洗成功响应

触发顺序如下：

1. `header_hook`
   - 每次请求尝试都会调用一次，包括重试
   - 调用时默认 `content-type: application/json` 已经写入
   - 如果 Provider 绑定了 `auth_group` 或 `api_key`，对应认证头也已经注入
   - 适合补充或覆盖 Header、Cookie、Token 等信息

2. `request_guard`
   - 在协议转换之前调用
   - 拿到的是下游原始请求体，而不是翻译给上游后的请求体
   - 适合改写 `messages`、补字段、调整 `stream`，或者做请求校验与护栏
   - 返回 `None` 时，代理会保留原始请求体

3. `response_guard`
   - 在协议转换之后调用
   - 处理的是“已经翻译成下游协议的数据”，不是原始上游响应
   - 非流式响应下，拿到的是完整的下游响应 payload
   - 流式响应下，会对每个下游 chunk 的 payload 调用一次；终止 chunk 不经过这个 Hook
   - 返回 `None` 时，代理会保留原内容

当前行为边界：

- `response_guard` 可以改写成功响应，但不会处理上游 `HTTP >= 400` 的错误响应；这类错误会按当前代理逻辑直接返回
- 对已经开始输出的流式响应，如果 `response_guard` 抛出异常，当前流会被中断，而不是再包装成标准错误响应
- 如果 `request_guard` 改写了 `body.stream`，后续请求与响应流程会按新的流式设置继续执行

适合用于：

- 注入额外 Header / Cookie / Token
- 在请求转发前做字段补充、删改和请求护栏
- 对成功响应做字段清洗、补全或内容改写
- 通过 `HookAbortError` 主动中止当前请求

`HookContext` 会提供这些上下文信息：

- `retry`：当前是第几次尝试，从 `0` 开始
- `provider_name`
- `request_model`：下游请求里的模型名，通常是 `provider/model`
- `upstream_model`：真正发往上游的模型名
- `provider_source_format`
- `provider_target_format`
- `transport`
- `stream`
- `auth_group_name`
- `auth_entry_id`
- `last_status_code`
- `last_error_type`

其中：

- 首次尝试时，`last_status_code` 和 `last_error_type` 都是 `None`
- 如果上一轮重试失败是 HTTP 状态码导致的，例如 `429`，下一轮会看到 `last_status_code`
- 如果上一轮失败是本地传输错误，例如超时、连接错误或 WebSocket 错误，下一轮会看到 `last_error_type`

## 数据与目录

- 配置文件：`config.yaml`
- 配置样例：`config.sample.yaml`
- Hook 目录：`hooks/`
- SQLite 默认库：`data/requests.db`
- 日志目录：`logs/`
- 架构文档：[docs/architecture-4plus1.md](docs/architecture-4plus1.md)

## 使用建议

- 公网 HTTPS 上游建议显式设置 `verify_ssl: true`
- 新增 Provider 时，先用后台“拉取模型”确认接口可达，再保存 `model_list`
- 多 Key 场景优先使用 `auth_group`，不要把轮询和限流逻辑下放到客户端
- 如果只是简单直连代理，不需要启用 Hook；只有在协议不兼容或鉴权不一致时再增加扩展逻辑

## 合规说明

Hook 和认证扩展能力的设计目标是支持合法授权前提下的协议兼容与私有集成。请仅在拥有访问权限的前提下使用相关能力，并遵守上游服务的产品条款、访问策略和安全要求。
