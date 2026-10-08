# 工具结果的跨协议媒体转换

项目在 OpenAI Chat Completions、OpenAI Responses 和 Claude Messages 三个协议之间转换请求与回包。下表按“客户端请求协议 → 上游协议”描述工具结果中的媒体传递方式。

| 转换方向 | 工具结果内容 | 媒体位置 |
| --- | --- | --- |
| Chat → Responses | 文本、图片、文件分别映射为 `input_text`、`input_image`、`input_file` | 对应工具调用的 `function_call_output.output` 或 `custom_tool_call_output.output` |
| Chat → Claude | 文本、图片、内联文件分别映射为 `text`、`image`、`document` | 对应工具调用的 `tool_result.content` |
| Responses → Chat | 工具消息保留文本；图片和内联文件映射为 Chat 内容块 | 全部并行 tool 消息之后的 user 消息 |
| Responses → Claude | 文本、图片、内联文件和 URL 文件映射为 Claude 内容块 | 对应工具调用的 `tool_result.content` |
| Claude → Chat | 工具消息保留文本；图片和内联文件映射为 Chat 内容块 | 全部并行 tool 消息之后的 user 消息 |
| Claude → Responses | 文本、图片、内联文件和 URL 文件映射为 Responses 内容块 | 对应工具调用的 `function_call_output.output` 或 `custom_tool_call_output.output` |

## 内容与关联

共享工具内容解析接受字符串、内容块数组和单个内容块。普通字符串和普通 JSON 字符串按原文保留。字符串中包含明确的图片或文件内容块时，服务解析该结构并按目标协议转换。Chat 客户端扩展的工具图片、文件数组使用相同转换链路。

媒体补传到 Chat user 消息时，每组媒体包含其工具调用 ID 的文字标记。一个并行调用批次的全部工具消息连续排列，媒体随后补传；批次内已有的 user 消息可以合并这些媒体。只有媒体的工具结果使用简短的文本占位内容。

Claude → Chat 按 Claude 的连续 user 消息合并语义处理同一批次的工具结果，分放在多条连续 user 消息中的并行工具结果保持连续排列。

Claude 文档的 `source.type=text` 内容映射为目标协议文本；`source.type=base64` 内容使用文件字段；`source.type=url` 内容使用 Responses 或 Claude 的 URL 文件来源。PDF 文件保留文件名，缺少文件名的 Claude 内联 PDF 使用 `document.pdf`。

未知的非媒体内容块保留为 JSON 文本。Claude 工具内容块中的 `cache_control` 提升到外层 `tool_result`，原始客户端请求保持原有内容。

同协议请求保留原有工具内容。模型是否支持图片、文件，以及文件 ID 的归属，由对应上游接口校验。

## 来源约束

标准 Chat tool 消息承载文本；图片和文件使用 user 内容块。Chat 文件内容支持内联数据或 OpenAI 文件 ID，URL 文件来源在跨协议转换时返回 HTTP 400，错误码为 `unsupported_tool_result_content`。

OpenAI 族协议之间可以保留文件 ID。OpenAI 文件 ID、图片文件 ID 或 Claude 文件 ID 映射到缺少兼容来源的目标时，服务返回相同的 HTTP 400 错误。调用方可以提供该文件的内联数据或目标协议支持的 URL 来源。

Claude 内联文档使用 PDF 或 UTF-8 文本来源。UTF-8 的 `text/*`、JSON 和 XML 文件映射为 `source.type=text`、`media_type=text/plain`；其他内联文档类型返回相同的 HTTP 400 错误。URL 文档使用 Claude 的 PDF URL 来源。

工具媒体块缺少有效来源时也返回 HTTP 400。普通 Provider、Codex OAuth 与 Claude OAuth 服务在发送模型上游请求前处理这类转换错误，认证文件的请求成功和失败状态由真实上游结果更新。

## 实现与验证

- `src/translators/tool_result_utils.py`：共享工具内容解析、目标内容转换和 Chat 媒体补传标记。
- `src/translators/registry.py`：Chat → Responses 与 Chat → Claude。
- `src/translators/claude_bridge.py`：Claude → Chat。
- `src/translators/responses_bridge.py`：Responses → Chat。
- `src/translators/responses_claude_bridge.py`：Claude ↔ Responses。
- `tests/test_tool_result_media.py`：六个方向、流式请求标志、并行调用、文本与 JSON、图片、PDF、文档来源、自定义工具和同协议内容。

转换方式参考 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI/tree/54946fa3dfa29c6ca7312ac141a92cdd5e413771) 的工具结果处理，并遵循目标协议的内容字段。工具输出中的图片和文件数组可参见 [OpenAI Responses 工具结果文档](https://developers.openai.com/api/docs/guides/function-calling#formatting-results)。
