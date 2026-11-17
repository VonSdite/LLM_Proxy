# LLM 数据记录

LLM 数据记录将客户端提交的对话输入和网关最终返回给客户端的输出保存为本地 JSONL，供后续清洗、筛选和模型训练使用。记录功能默认关闭，不提供记录状态、计数、浏览、清理、下载或训练导出页面。

## 启用与目录

系统设置中的“LLM 数据记录”包含：

- **开启 LLM 数据记录**：默认关闭。
- **保存目录**：仅在开启记录后显示，仅接受运行 LLM Proxy 的服务器文件系统绝对路径。POSIX 示例为 `/var/lib/llm-proxy/llm-records`，Windows 示例为 `D:\llm-proxy\llm-records`。

未配置 `llm_recording.directory` 或该值为空时，系统设置显示项目根目录下 `data/llm-records` 的绝对路径。显式保存目录时必须填写绝对路径，相对路径会被拒绝。保存设置会验证目录可创建并可写，校验失败时保留原配置。

```yaml
llm_recording:
  enabled: false
  directory: /var/lib/llm-proxy/llm-records
```

配置保存后，新请求使用当时的开关和目录快照。关闭开关后，已开始采集的请求继续完成写入；切换目录后，在途请求仍写入原目录。历史文件不会自动搬迁或删除。默认关闭时不会创建记录目录，也不会保留正文采集快照。

记录包含完整提示词、历史消息、工具结果和模型输出，可能包含敏感业务内容。保存目录的访问权限、备份和搬迁流程应按训练数据保护要求管理。

## 采集范围

记录覆盖以下下游接口：

- `POST /v1/chat/completions`
- `POST /v1/responses`
- `POST /v1/messages`

普通 Provider、Codex OAuth、Claude OAuth、模型映射、内部重试和目标回退共用一次客户端请求一条记录的语义。流式与非流式响应、文本、工具调用、工具参数碎片、请求携带的工具结果、拒绝、reasoning/thinking 和多候选输出均按对应协议结构保存。

以下能力不生成 LLM 数据记录：

- `POST /v1/images/generations`
- `POST /v1/images/edits`
- `POST /v1/responses/compact`

采集输入是客户端提交的请求内容。采集输出是经过协议适配、Hook 和隐私还原后最终向客户端发送的内容，不等同于上游实际收到的完整原始报文。系统不分别保存上下游四份报文。

## 记录字段

每次请求写入一个 JSON 对象，JSONL 文件中的一行对应一条记录。

| 字段 | 语义 |
| --- | --- |
| `schema_version` | 数据格式版本，当前为 `1` |
| `time` | 请求开始时间，包含本地时区偏移 |
| `request_model` | 产生最终下游结果的上游请求体实际 `model`；未发起上游请求时为 `null` |
| `response_model` | 上游响应实际提供的模型 ID；上游未提供时为 `null` |
| `upstream_protocol` | 产生最终下游结果的实际上游协议族：`openai_chat`、`openai_responses` 或 `claude_chat`；未发起上游请求或缺少观测元数据时为 `null` |
| `generation_params` | 请求实际提供的生成参数，不补服务端默认值 |
| `input` | 输入上下文数组，保留消息位置、工具结果和上下文引用 |
| `output` | 最终下游输出，保留候选、工具调用、推理与拒绝结构 |
| `finish_reason` | 停止原因；多候选 Chat 请求在顶层为 `null`，各候选分别保存 |
| `capture_status` | 采集完整性状态 |
| `capture_issues` | 存在完整性问题时保存的精简标记数组 |
| `tools` | 请求实际提供时保存工具定义 |
| `tool_choice` | 请求实际提供时保存工具选择策略 |
| `response_format` | 请求实际提供时保存输出格式约束；Responses 的 `text.format` 与 Claude 的 `output_config.format` 也归入此字段 |

`generation_params` 当前读取请求中实际存在的 `temperature`、`top_p`、`top_k`、`max_tokens`、`max_completion_tokens`、`max_output_tokens`、`stop`、`stop_sequences`、`presence_penalty`、`frequency_penalty`、`logit_bias`、`logprobs`、`top_logprobs`、`n`、`seed`、`reasoning_effort`、`reasoning`、`thinking`、`verbosity`、`service_tier` 和 `parallel_tool_calls`。

记录不包含用户身份、IP、HTTP 头、凭据、Token 用量、耗时、吞吐或内部重试过程。现有运行日志和请求统计继续承担相应职责。

### 模型 ID 规则

- 客户端模型映射 ID、普通 Provider 路由前缀不作为 `request_model`。
- `request_model` 读取最终上游请求体的 `model`，包括 Hook 对请求模型的改写。
- Provider 前缀只移除路由部分，真实模型名中的其他斜杠保持原样。
- 内部重试或模型映射回退使用最终产生下游结果的上游请求模型。
- `response_model` 只读取上游实际响应，不使用请求模型或下游转换器默认值推测补齐。

`upstream_protocol` 与客户端调用的下游接口协议独立。普通 Provider 使用其 `source_format`，Codex OAuth 使用 `openai_responses`，Claude OAuth 使用 `claude_chat`。内部重试或回退与 `request_model` 使用同一次最终上游调用的协议，不根据下游路由推测。

### 三种协议的输入与输出

**OpenAI Chat Completions**

- `input` 按原顺序保存 `messages`。
- `output` 的每项保存一个候选的 `message`，并增加 `choice_index` 与该候选的 `finish_reason`。
- 单候选的顶层 `finish_reason` 与该候选一致；多候选时顶层为 `null`，不会把多个候选串成多轮对话。
- 模型返回的结构化 JSON 正文仍保存为 `content` 字符串。

**OpenAI Responses**

- `instructions` 以 `source=responses.instructions` 的提示词引用保存。
- `previous_response_id` 保存为 `type=response_reference` 的输入项，包含 `context_complete=false`，并添加 `referenced_context_missing` 标记；网关未携带的隐藏历史不会被视为已采集。
- `output` 保留原生 output items，包括 message、reasoning、function_call 和 custom_tool_call。
- 终态 output 与 delta 按 output item 合并，避免重复文本。
- `finish_reason` 使用 `incomplete_details.reason`；没有该值时使用响应状态，例如 `completed`、`failed` 或 `cancelled`。

**Claude Messages**

- 顶层 `system` 以 `source=claude.system` 的提示词引用保存，随后按原顺序保存 `messages`。
- `output` 保存一个带 `role` 的对象，`content` 保留原生 text、thinking、tool_use、refusal 等内容块。
- `finish_reason` 保存 `stop_reason`；流式工具参数不是完整 JSON 时保留 `partial_json`，不伪造完整工具输入。

### 示例

以下示例展开显示，实际文件中一条记录占一行：

```json
{
  "schema_version": 1,
  "time": "2026-11-17T14:30:00+08:00",
  "request_model": "gpt-4.1",
  "response_model": "gpt-4.1-2025-04-14",
  "upstream_protocol": "openai_chat",
  "generation_params": {
    "temperature": 0.7,
    "max_completion_tokens": 1024
  },
  "response_format": {"type": "json_object"},
  "input": [
    {"prompt_ref": "sha256:<提示词SHA256>"},
    {"role": "user", "content": "用 JSON 回答：Python 生成器是什么？"}
  ],
  "output": [
    {
      "role": "assistant",
      "content": "{\"explanation\":\"生成器通过 yield 按需产生值。\"}",
      "choice_index": 0,
      "finish_reason": "stop"
    }
  ],
  "finish_reason": "stop",
  "capture_status": "complete"
}
```

## 采集完整性

`capture_status` 的取值：

| 值 | 含义 |
| --- | --- |
| `complete` | 输入、输出与协议终止信息完整，且没有未归档媒体或隐藏上下文引用 |
| `interrupted` | 客户端取消、响应关闭、协议终止事件缺失或 Responses 明确取消 |
| `failed` | 请求失败、下游错误响应或流内错误事件 |
| `truncated` | 输入或输出达到采集边界，保留内容不完整 |
| `parse_error` | 下游正文、SSE 或 UTF-8 内容无法按预期解析 |
| `partial` | 数据可解析，但存在未归档媒体、未包含的引用上下文或部分工具参数等明确缺口 |

`finish_reason=length`、`max_tokens` 或对应协议的长度限制值描述模型停止原因，不等同于采集截断。采集是否完整由 `capture_status` 独立表示。

当前 `capture_issues` 包括：

- `input_truncated`
- `output_truncated`
- `response_parse_failed`
- `stream_terminal_missing`
- `incomplete_tool_arguments`
- `referenced_context_missing`
- `media_not_archived`
- `media_body_omitted`

图片、音频、视频和文件正文不归档。媒体结构、外部引用与可用元数据保留，媒体对象增加 `capture_media_archived=false`。内嵌正文或 data URL 由包含 `archived=false`、`reason=media_not_archived` 和内容 SHA-256 的对象替换；包含媒体的记录不会标为 `complete`。

## 提示词去重

目录结构：

```text
<保存目录>/
  prompts/
    <SHA256>.json
  records/
    <YYYY-MM-DD>-<instance>-<6位序号>.jsonl
```

Chat 的 system/developer 消息、Responses instructions 和 Claude system 使用 `prompt_ref` 引用独立提示词文件。引用保留在原输入数组位置，Responses 与 Claude 的引用另带 `source`，不会把 instructions 当作普通用户消息。

提示词文件包含：

```json
{
  "role": "system",
  "content": "你是一名 Python 编程助手。请使用中文回答。"
}
```

SHA-256 覆盖 `role` 与完整 `content`。规范化采用 UTF-8、键排序和紧凑 JSON 序列化；字符串空格、换行、数组顺序和其他影响语义的内容均参与哈希。只有完全相同的内容复用同一文件，不执行模糊合并。

提示词通过同目录临时文件和原子替换落盘，先于引用它的记录写入。写入线程使用最多 1024 项的有界缓存减少文件检查，重启后仍按内容哈希复用现有文件。导出或搬迁数据集时必须同时保留 `prompts/` 与 `records/`。

## 并发、边界与退出

```text
多个并发请求
  -> 各自独立的有界采集快照
  -> 有界原生队列
  -> 单个专用原生写入线程
  -> 协议聚合、提示词去重、JSON 序列化与文件落盘
```

请求 greenlet 只复制有界输入并追加最终下游字节，不执行记录文件磁盘 I/O。协议聚合、哈希、JSON 序列化和文件写入在专用原生线程执行；该线程仍有 Python GIL 成本。流式响应边转发边采集，不等待完整回答后再向客户端输出，也不会把每个 SSE 片段写成独立样本。

内部边界：

| 项目 | 上限 |
| --- | --- |
| 单请求输入快照 | 8 MiB |
| 单请求输出字节 | 8 MiB |
| 全部活跃采集快照与输出 | 64 MiB |
| 待写队列 | 256 条及 64 MiB |
| JSONL 分片 | 约 128 MiB |

输入或输出超过单条边界时保留明确截断状态。活跃采集总内存不足时跳过新采集。队列满、磁盘满、权限异常或写入失败会放弃对应记录并输出有节制的英文应用日志，不影响代理响应。

JSONL 使用 UTF-8 无 BOM 和 LF。文件名包含请求本地日期、进程实例标识和序号。已有文件永不覆盖或续写；进程重启和固定实例标识下的序号冲突都会使用新的分片，因此崩溃留下的不完整尾行保持可识别，不会在其后直接拼接新记录。

应用正常退出先停止接收新请求，再排空记录队列并关闭分片，随后关闭应用日志线程。强制终止可能丢失未落盘数据。记录存储独立于运行日志队列和请求统计数据库，不承诺审计级零丢失。
