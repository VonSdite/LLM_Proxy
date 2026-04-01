# LLM Proxy

一个面向多上游协议、少量稳定下游协议面的 LLM 代理服务。

当前版本按“首版干净架构”实现，只保留 OpenAI / Claude / Codex 三个协议家族，不保留旧字段、旧别名或 Gemini 相关兼容逻辑。

## 特性

- 上游协议只保留 4 个 `source_format`
  - `openai_chat`
  - `openai_responses`
  - `claude_chat`
  - `codex`
- 下游协议只保留 4 个 `target_format`
  - `openai_chat`
  - `openai_responses`
  - `claude_chat`
  - `codex`
- 对外路由只保留：
  - `POST /v1/chat/completions`
  - `POST /v1/responses`
  - `POST /v1/messages`
  - `GET /v1/models`
- 统一代理链路：
  - `executor -> decoder -> translator -> guard -> encoder`
- Provider 公共配置只保留：
  - `name`
  - `api`
  - `transport`
  - `source_format`
  - `target_format`
  - `api_key`
  - `proxy`
  - `timeout_seconds`
  - `max_retries`
  - `verify_ssl`
  - `model_list`
  - `hook`
- 用户扩展点只保留：
  - `header_hook`
  - `request_guard`
  - `response_guard`

## 下游协议面

| target_format | route | 典型客户端 |
| --- | --- | --- |
| `openai_chat` | `POST /v1/chat/completions` | OpenCode、Cherry Studio(OpenAI provider)、通用 OpenAI-compatible SDK |
| `openai_responses` | `POST /v1/responses` | Responses API 客户端、自研 agent |
| `codex` | `POST /v1/responses` | Codex |
| `claude_chat` | `POST /v1/messages` | Claude Code、Cherry Studio(Anthropic provider) |

注意：

- `target_format` 和下游 route 必须严格对齐。
- 一个 provider 配成 `openai_chat` 后，不能再被 `/v1/responses` 或 `/v1/messages` 混用。
- `/v1/responses` 同时承接 `openai_responses` 和 `codex`。
- `/v1/models` 会额外返回每个模型对应的 `source_format`、`target_format` 和 `transport`，方便下游做能力发现。

## 上游怎么选 `source_format`

先看上游真实接口的 URL 和请求体，再选 `source_format`：

| 上游接口形态 | source_format |
| --- | --- |
| `/v1/chat/completions`，请求体主字段是 `messages` | `openai_chat` |
| `/v1/responses`，请求体主字段是 `input` / `instructions` | `openai_responses` |
| `/v1/messages` | `claude_chat` |
| Codex / Responses-only 变体，需要 Codex 特化请求整形 | `codex` |

常见例子：

- vLLM / OpenRouter / Ollama OpenAI 兼容层：通常选 `openai_chat`
- OpenAI Responses API：选 `openai_responses`
- Anthropic Messages API：选 `claude_chat`
- Codex：选 `codex`

## `codex` 和 `openai_responses` 的关系

`codex` 不是独立路由族，它是 OpenAI Responses 家族下的特化标签：

- 和 `openai_responses` 共用 `/v1/responses`
- 和 `openai_responses` 共用 Responses encoder
- 但保留 Codex 请求整形差异，例如：
  - `system` -> `developer`
  - `store = false`
  - `parallel_tool_calls = true`
  - `include = ["reasoning.encrypted_content"]`

## Provider 配置

### Auth Groups

```yaml
auth_groups:
  - name: openai-shared
    strategy: least_inflight
    cooldown_seconds_on_429: 60
    entries:
      - id: key-a
        headers:
          Authorization: Bearer sk-your-openai-key-a
        max_concurrency: 3
      - id: key-b
        headers:
          Authorization: Bearer sk-your-openai-key-b
        max_concurrency: 2
```

规则：

- provider 可以引用 `auth_group`，也可以继续用 legacy `api_key`
- `auth_group` 和 `api_key` 不能同时填写；也可以都不填，交给 hook 或无鉴权上游处理
- `api_key` 仍然保留为 legacy 单 key 快捷写法
- `auth_group` 支持并发控制、`429` 冷却、请求数配额、Token 配额
- `401/403` 会禁用当前 entry，直到在管理页手动恢复

### 样例

见 [config.sample.yaml](/d:/001Code/008llm/003LLM_Proxy/config.sample.yaml)。

最小 provider 例子：

```yaml
providers:
  - name: openai-chat
    api: https://api.openai.com/v1/chat/completions
    transport: http
    source_format: openai_chat
    target_format: openai_chat
    auth_group: openai-shared
    verify_ssl: true
    model_list:
      - gpt-4.1
```

### 字段说明

- `name`
  - provider 名称，模型会以 `provider/model` 的形式暴露给下游
- `api`
  - 真实上游地址
- `transport`
  - `http` 或 `websocket`
- `source_format`
  - 上游真实协议
- `target_format`
  - 下游看到的协议
- `auth_group`
  - 引用顶层 `auth_groups` 里的凭证池
- `api_key`
  - legacy 单 key 快捷写法；不能和 `auth_group` 同时填写，也可以两者都留空
- `model_list`
  - 这个 provider 暴露给下游的模型名列表
- `hook`
  - Hook 文件名，放在 `hooks/` 下

### 不再支持的字段

当前版本会直接拒绝这些旧字段：

- `format`
- `stream_format`
- 任何 Gemini / Antigravity 相关格式值

## 流式处理

`stream_format` 已经不是公共配置项。

代理内部会自动识别上游响应：

- WebSocket 上游：按 JSON 消息流处理
- HTTP `text/event-stream`：按 SSE JSON 处理
- `application/x-ndjson` / `ndjson` / `jsonl`：按 NDJSON 处理
- 其他 HTTP：按非流式处理
- 如果声明不是 SSE，但首块看起来像 SSE，内部会做一次首块探测兜底

这部分是实现细节，不需要用户手工选择。

## Hook

补充：当前 hook 还可以感知“上一轮 retry 的失败摘要”，适合做最基础的多 key 轮换判断。

`header_hook` 里可以直接读取：

- `ctx.retry`
- `ctx.last_status_code`
- `ctx.last_error_type`

`last_error_type` 的类型是 `HookErrorType`，当前可用值：

- `HookErrorType.TIMEOUT`
- `HookErrorType.CONNECTION_ERROR`
- `HookErrorType.WEBSOCKET_ERROR`
- `HookErrorType.TRANSPORT_ERROR`

最小示例：

```python
from src.hooks import BaseHook, HookErrorType


class Hook(BaseHook):
    def header_hook(self, ctx, headers):
        if ctx.last_status_code == 429:
            headers["X-Retry-Reason"] = "rate_limit"
        elif ctx.last_error_type == HookErrorType.TIMEOUT:
            headers["X-Retry-Reason"] = "timeout"
        return headers
```

Hook 示例见 [hooks/example_hook.py](/d:/001Code/008llm/003LLM_Proxy/hooks/example_hook.py)。

支持的接口：

```python
class Hook(BaseHook):
    def header_hook(self, ctx, headers):
        return headers

    def request_guard(self, ctx, body):
        return body

    def response_guard(self, ctx, body):
        return body
```

`HookContext` 里和协议相关的字段是：

- `provider_name`
- `request_model`
- `upstream_model`
- `provider_source_format`
- `provider_target_format`
- `transport`
- `stream`
- `auth_group_name`
- `auth_entry_id`
- `last_status_code`
- `last_error_type`

补充的重试语义：

- 第一次 attempt 时，`last_status_code` 和 `last_error_type` 都是 `None`
- 只有当上一轮失败后真的进入了下一次 retry，hook 才会在新的 `ctx` 里看到上一轮失败摘要
- `last_status_code` 表示上一轮拿到了 HTTP 状态码，例如 `429`、`500`
- `last_error_type` 表示上一轮没有 HTTP 状态码，而是本地传输异常

推荐分工：

- `translator`
  - 做协议适配
- `guard`
  - 做安全审查、护栏、审计、脱敏

## Provider 页面

Provider 管理页现在拆成两块：

- `Auth Groups`
  - 维护 auth_group / auth_entry
  - 查看运行态、冷却、禁用和配额
- `Providers`
  - 维护 provider 与它绑定的 `auth_group`、legacy `api_key`，或留空交给 hook

页面上会继续提供 3 个帮助入口：

- `transport`
- `source_format`
- `target_format`

点击 `?` 会解释：

- 上游 / 下游分别是什么意思
- 怎么从 URL 和请求体判断 `source_format`
- 哪些客户端通常对应哪个 `target_format`

## 运行

```bash
python main.py
```

默认监听配置文件里的 `server.host` 和 `server.port`。

## 测试

```bash
python -m unittest discover -s tests
python -m compileall src tests hooks
```

## 目录

- [src/services/proxy_service.py](/d:/001Code/008llm/003LLM_Proxy/src/services/proxy_service.py)
  - 主代理链路
- [src/translators/registry.py](/d:/001Code/008llm/003LLM_Proxy/src/translators/registry.py)
  - 四个协议族之间的 translator registry
- [src/config/provider_config.py](/d:/001Code/008llm/003LLM_Proxy/src/config/provider_config.py)
  - Provider 公共配置模型
- [src/presentation/proxy_controller.py](/d:/001Code/008llm/003LLM_Proxy/src/presentation/proxy_controller.py)
  - 下游路由入口
- [src/presentation/templates/providers.html](/d:/001Code/008llm/003LLM_Proxy/src/presentation/templates/providers.html)
  - Provider 管理页
- [docs/architecture-4plus1.md](/d:/001Code/008llm/003LLM_Proxy/docs/architecture-4plus1.md)
  - 4+1 架构说明
