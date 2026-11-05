# Agent 模型参数表维护

模型参数统一维护在 [`src/config/agent_model_limits.json`](../src/config/agent_model_limits.json)。Agent 页面从本项目读取这份表，Claude、Codex 和 opencode 共用同一套模型匹配与候选数据。表内初始数据参考 LLM-Switch，后续维护直接在本项目完成。

## 字段与用途

表的顶层是 JSON 数组，每个对象代表一组参数相同的模型。

| 字段 | 含义 | 维护规则 |
| --- | --- | --- |
| `ids` | 模型 ID 列表 | 至少一个；使用小写基础 ID，省略 Provider 前缀和 `[1m]` 后缀 |
| `aliases` | 可选别名列表 | 别名与对应模型使用同一套参数；名称在整份表中唯一 |
| `label` | 该组参数的来源说明 | 使用可识别的模型名称或资料名称 |
| `context` | 默认上下文参考值，单位 Token | 与 `candidates.context` 第一项的 `value` 一致 |
| `output` | 默认最大输出参考值，单位 Token | 与 `candidates.output` 第一项的 `value` 一致 |
| `compact` | 可选的默认压缩比例，单位 % | 填写时与 `candidates.compact` 第一项一致 |
| `candidates.context` | 上下文候选列表 | 按推荐展示顺序填写，保留客户端与模型官方容量等不同候选 |
| `candidates.compact` | 压缩比例候选列表 | 取值 1–100；这是客户端使用策略，Agent 的实际允许范围由其配置规则决定 |
| `candidates.output` | 最大输出候选列表 | 使用模型支持或客户端适用的输出预算 |

每个候选的字段：

| 字段 | 含义 |
| --- | --- |
| `value` | 正整数数值；Token 使用完整整数，例如 `200000` |
| `kind` | `official` 官方规格、`client-default` 客户端默认、`derived` 计算结果、`fallback` 通用参考 |
| `label` | 推荐菜单中的短说明，例如“客户端默认上下文” |
| `sourceUrl` | 可选的资料地址，使用 HTTP(S) URL |
| `checkedAt` | 可选的资料核对日期，格式 `YYYY-MM-DD` |

官方数值建议附带资料地址与实际核对日期。多个 ID 的规格不同时，分别建立表项。顶层的 `context` 和 `output` 还用于 opencode 模型目录生成；推荐菜单使用 `candidates`，两者的第一候选保持一致。

## 已核对型号与参数口径

2026-11-05 核对的补充范围包括 Kimi K3 / K3.1 Pro、腾讯混元 Hy4 / Hy3、豆包 Seed Evolving / Seed 2.1 / Seed 2.0 LTS、小米 MiMo V2.6、Gemini 4 Argon / 3.8 Flash、Grok 4.7、Mistral Large 4、Qwen3.7 Max / Qwen3 Coder Flash 和 GPT-5.3-Codex。具体 API ID、参数来源和核对日期记录在 JSON 表内。

- Kimi K3.1 Pro 的普通版和高速版使用同一组官方参数。火山方舟的 `kimi-k3-1-pro-261024` 使用独立表项，保留该平台的最大回答与 Chat API 默认回答候选。
- 豆包使用官方的完整版本 ID。`doubao-seed-2-1-pro-260628` 的上下文为 256K，`doubao-seed-2-1-pro-260915` 为 1024K，两者分别记录。`doubao-seed-evolving` 单独维护，候选对应核对日期的规格。
- Gemini 4 Argon 的输入与输出共享总窗口，普通同步请求的输出上限为 65,536。1M 长解码需要专门的调用能力，常规 Agent 输出候选采用同步请求上限。Gemini 3.8 Flash 的窗口候选标明官方最大输入。
- Kimi 与 MiMo 的输出预算包含思考内容；豆包候选中的“最大回答”按模型目录的回答口径记录。实际输出还受剩余上下文、接口和客户端规则约束。
- `grok-4.7`、`mistral-large-4`、`qwen3.7-max`、`qwen3-coder-flash` 的官方资料已确认上下文；最大输出候选保留 32,000 的 `fallback` 通用预算，标签标明“官方上限待确认”。压缩比例同样以客户端参考策略记录。

## 添加模型

在数组中添加一个对象。以下模型名与数值仅用于展示格式，维护时填写目标模型的实际参数：

```json
{
  "ids": ["example-chat-1"],
  "aliases": ["example-chat-latest"],
  "label": "Example Chat 1 参数参考",
  "context": 200000,
  "compact": 60,
  "output": 32000,
  "candidates": {
    "context": [
      {"value": 200000, "kind": "fallback", "label": "上下文参考值"}
    ],
    "compact": [
      {"value": 60, "kind": "fallback", "label": "建议压缩阈值"}
    ],
    "output": [
      {"value": 32000, "kind": "fallback", "label": "最大输出参考值"}
    ]
  }
}
```

同一模型有多个适用窗口时，在 `candidates.context` 中按展示顺序追加候选。候选的 `label` 和 `kind` 区分官方规格、客户端默认值与通用参考；第一项对应顶层 `context`。最大输出和压缩比例采用相同规则。

在仓库根目录执行校验：

```sh
uv run python scripts/check_agent_model_limits.py
```

校验覆盖 JSON 重复字段、模型名称冲突、数值类型、百分比范围、重复候选、来源字段格式和第一候选一致性。校验读取本地表，数值是否符合目标模型的实际能力由维护时核对的资料确定。

维护完成后重启 LLM_Proxy 并刷新 Agent 页面。页面在模型匹配后显示新候选，点击候选时填入参数；已保存的 Agent 配置继续保留原值。

## 名称匹配与路由别名

匹配顺序为模型 ID、别名、主 ID 的日期快照。模型匹配忽略输入名称的大小写，并移除 Provider 前缀及 `[1m]` 后缀，同时支持点号与连字符的常见写法。

例如，`main/example-chat-1` 对应表中的 `example-chat-1`；`example-chat-1-20261105` 可以通过日期快照规则命中基础模型。快照规格有差异时，为该快照 ID 单独添加表项，精确匹配优先。

这份表描述“模型名称对应什么参数”，后台“模型映射”描述“请求发给哪个上游”。Agent 参数推荐当前按选中的模型 ID 查表。固定指向同一规格模型的路由别名，可以加入该模型的 `aliases`；参数不同的多目标路由可使用单独表项，记录该路由适用的参数。新增表项只增加参数候选，模型是否出现在下拉框仍由 Provider、OAuth 和模型映射目录决定。

未识别的模型显示通用候选；表内上下文容量与 Claude 的压缩窗口分别表示模型能力和客户端配置，实际生效范围由 Agent 决定。
