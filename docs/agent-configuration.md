# Agent 配置与兼容性

本文汇总 Agent 扩展格式、配置兼容边界和模型参数表维护方法。声明式定义以 `agents/<id>/agent.yaml` 为准，适配行为以同目录 `adapter.py` 和 `src/services/agent_config_service.py` 为准，模型参数以 `src/config/agent_model_limits.json` 为准。

## 1. 扩展注册与文件目标

项目根目录的 `agents/<id>/agent.yaml` 是 Agent 注册入口。服务启动时扫描目录，管理页面从 `/api/agent-config/bootstrap` 接收定义。新增声明式 Agent 后重启 Python 服务即可使用，通用前端构建产物不需要增加 Agent ID 分支。

目录名与 `id` 一致，使用小写字母开头的字母、数字、下划线或短横线，最长 64 字符。文件目标 ID 在全部扩展中唯一，Agent 默认顺序由 `order` 决定，用户的页签顺序和显隐保存在当前浏览器。

一个目录加载失败时，其他 Agent 继续可用；页面展示扩展错误，应用日志记录英文错误。扩展目录位于 Python 服务所在机器，SSH 目标机器保存实际被编辑的配置。更新仓库时保留用户自己的扩展目录。

新建配置的预设仅在目标文件不存在时应用；已有空文件或缺失字段继续显示“不设置”。Opencode v1 使用 `share: disabled`、`autoupdate: false`；Opencode v2 使用 `share: disabled`、`update: disable`；Hermes 使用 `memory.memory_enabled: true`、`memory.user_profile_enabled: true`。Claude 预设见下文。

### 1.1 最小声明

```yaml
id: my_agent
name: My Agent
order: 100
files:
  my_agent:
    path: ~/.my-agent/config.yaml
    format: yaml
groups: {connection: 2, model: 2}
fields:
  - id: base-url
    label: API 地址
    component: url
    role: baseUrl
    group: connection
    file: my_agent
    path: [api, base_url]
    required: true
    help: [填写 Agent 可访问的 OpenAI 兼容服务基础地址。]
  - id: api-key
    label: API Key
    component: secret
    group: connection
    file: my_agent
    path: [api, api_key]
    help: [服务访问凭据，保存预览中打码显示。]
  - id: model
    label: 模型
    component: model
    group: model
    file: my_agent
    path: [model]
    required: true
    help: [本服务使用模型下拉，其他地址手动输入模型 ID。]
```

只有 JSON、JSONC、TOML、YAML 映射文件或 dotenv 键值文件，且字段可直接定位时，单个声明文件即可完成接入。字段路径由目标 Agent 的实际格式决定。

### 1.2 字段定义

| 字段 | 含义 |
| --- | --- |
| `files.<target>.path` | 默认文件路径；`~/` 使用目标机器的用户目录 |
| `format` | `json`、`jsonc`、`toml`、`yaml`、`dotenv` |
| `homeEnv` / `homeRelative` | 本机环境变量指定目录时，相对该目录的文件路径段；SSH 使用远程默认路径或用户自定义路径 |
| `groups` | 分组 ID 与宽屏列数（1–4），窄屏自动收为两列或一列 |
| `fields` | 有序配置项；保存预览按表单顺序跨文件排列 |
| `id` / `label` | 表单字段 ID / 用户看到的标题 |
| `file` / `path` | 文件目标与字段路径数组；路径段中的点和斜杠保持原样 |
| `component` | `text`、`secret`、`url`、`model`、`select`、`boolean`、`number` |
| `role: baseUrl` | 该 URL 决定是否显示本服务模型与 API Key 候选 |
| `options` | 下拉选项数组，每项包含字符串 `value`、`label`；`text` 可搜索并手填 |
| `help` | 问号气泡中的说明列表 |
| `default` | 文件不存在时的默认值；必填选项缺失时也使用此值 |
| `required` | 输入不可为空；原文件的复杂自定义值可保留 |
| `min` / `max` / `step` | 数值范围与步长；数值失焦或 Enter 后提交草稿 |
| `integer: false` | 允许小数，配合 `step` 使用 |
| `limit` | `context` 或 `output`，提供当前模型的容量参考候选 |
| `showWhen` | `{field: other-id, equals: value}`，匹配时显示和参与保存；隐藏字段保留原值 |
| `constants` | 按文件声明固定写入项，格式为 `{path: [...], value: ...}` 的数组 |

原文件已有值优先回显；缺失的可选字段显示“不设置”。不支持的复杂对象显示“保留自定义配置”，未操作时保持原值。主动清空可选项表示删除字段。只修改声明路径，不清理未声明字段。缺失的 API 地址与必填模型使用本服务候选；外部地址的模型 ID 保留手动输入能力。

Opencode v2、DSH 和 Hermes 的思考强度选项与选中值直接显示配置值，例如 `minimal`、`high`、`xhigh`；清空配置的选项显示“不设置”。

dotenv 文件的字段路径是单个环境变量名，解析支持可选 `export` 前缀、成对单双引号和未加引号值的行尾注释。编辑保留注释、空行和未声明行；写入值包含空白或特殊字符时使用双引号，同名变量的后续行随写入或删除一并移除，避免旧值覆盖本次写入。

声明式扩展的 `default` 不用于存放真实密钥，定义会返回给已登录的管理页面。

### 1.3 适配器接口

同目录的 `adapter.py` 可覆盖以下函数，未实现的函数采用公共行为：

| 函数 | 职责 |
| --- | --- |
| `parse(target, text) -> dict` | 原文转换为字段路径可寻址的结构 |
| `edit(target, text, edits) -> str` | 应用路径到值的修改，返回原生格式文本 |
| `visible(target, data) -> dict` | 提取表单字段；默认按声明路径返回字段 ID 到值的映射 |
| `prepare(draft, fields) -> dict` | 在声明式校验完成后补充跨字段编辑计划 |
| `display_path(target, path) -> str` | 把虚拟字段路径映射为保存预览中的原生配置路径 |
| `desired(draft, catalog) -> dict` | 完整替代草稿校验及编辑计划生成，适用于专用表单 |

`fields` 的结构为 `{target: {tuple(path): value}}`，删除使用 `src.services.agent_config_documents.MISSING`。文件读写、SSH、版本冲突、预览票据、5 份备份和失败回滚由 `AgentConfigService` 统一管理。

各 Agent 表单支持文件冲突后的覆盖确认：保留草稿，读取最新文件版本生成字段预览，用户确认后使用该预览票据保存；未管理字段保留，确认期间的外部修改仍受版本校验保护。预览错误旁提供小号刷新图标按钮，加载当前 Agent 的最新文件内容；有未保存草稿时先确认放弃。字段校验错误不进入覆盖流程。

适配器是以服务权限运行的受信任 Python 代码。可从 `src.services.agent_extensions` 导入 `yaml_load`、`yaml_dump`、`apply_edits`，复用保留注释的 YAML 读写。普通声明式扩展无需 Python 文件。

## 2. Claude Code 兼容性

行为核对日期为 2026-11-05，署名配置核对日期为 2026-11-07，版本依据核对日期为 2026-11-12。本机核对版本为 Claude Code `2.1.310`；SSH 目标机器的实际行为取决于其 Claude Code 版本、配置作用域、模型能力和组织策略。

“最低支持版本”仅用于官方发布记录明确说明新增的字段或当前写入方式。“已提及”不能证明字段首次支持版本。界面版本文案集中在 `frontend/src/features/agents/versionHelp.ts`。

### 2.1 字段与写入行为

| 配置项 | 当前行为与页面管理 |
| --- | --- |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL`、`ANTHROPIC_DEFAULT_OPUS_MODEL`、`ANTHROPIC_DEFAULT_SONNET_MODEL`、`ANTHROPIC_DEFAULT_FABLE_MODEL`、`ANTHROPIC_MODEL` | 五项独立编辑；“一键设置模型”即时同步，单项不一致时顶部留空；各项支持“不设置”，“一键设置模型”选择“不设置”同时清空五项，保存时按 [环境变量清除流程](#24-环境变量清除与当前会话) 删除覆盖 |
| `ANTHROPIC_REASONING_MODEL` | 官方环境变量文档与本机 `2.1.310` 均未确认当前支持，常规模型表单不管理该字段 |
| `IS_SANDBOX` | 识别运行环境，本身不创建沙箱；页面独立管理 `env.IS_SANDBOX`，启用写 `1`、禁用写 `0` |
| `CLAUDE_CODE_ATTRIBUTION_HEADER` | 控制客户端标识；启用写 `1`，禁用写 `0` |
| `attribution` | 控制 Git 提交、PR 描述及会话链接署名；支持对象或 `false`，`true` 无效；`false` 需要 `2.1.281+` |
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | 任意非空值均禁用，包括 `0`、`false`；页面禁用写 `1`，允许时移除当前文件变量 |
| `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` | 实验性 Agent Teams；启用写 `1`，禁用写 `0`；功能发布版本为 `2.1.32` |
| `ENABLE_TOOL_SEARCH` | 支持 `true`、`false`、`auto`、`auto:N`；页面设置布尔值，已有自动阈值未操作时保留 |
| `skipWebFetchPreflight` | 顶层布尔字段；`true` 跳过 WebFetch 域名安全预检，不改变 WebFetch 与 WebSearch 各自权限 |
| `CLAUDE_CODE_EFFORT_LEVEL` | 支持强度档位及 `auto`；`unset` 按等价的 `auto` 展示；`2.1.111` 新增 xhigh，仍取决于模型 |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | 范围 `100000`–`1000000`，同时受模型实际窗口限制；`2.1.304` 记录 k / M 后缀，本页保存整数 |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | 接受 `1`–`100`，只能提前压缩，高于默认阈值时忽略 |
| `CLAUDE_CODE_MAX_OUTPUT_TOKENS` | 默认值和上限随模型变化；无法识别的模型默认 32000、上限 128000 |
| `NO_PROXY` | 官方列出的绕过代理地址列表；页面管理 `env.NO_PROXY` 单行文本，支持“填入本服务地址”合并 API 地址主机名，清空后显式移除 |
| `NODE_TLS_REJECT_UNAUTHORIZED` | Node.js 运行时变量，官方环境变量文档未列出；跳过证书校验写 `0`，强制校验写 `1`，其他写法显示为文件自定义 |

Opus / Sonnet 默认模型变量在 `1.0.88` 新增；Haiku 变量在 `2.0.17` 发布记录中出现。命令沙箱在 `2.0.24` 发布 Linux / macOS 版本。对象形式的署名配置在 `2.0.62` 引入，禁用值 `false` 在 `2.1.281` 引入。工具自动搜索在 `2.1.7` 默认开启，`2.1.9` 引入 `auto:N`，`2.1.72` 修复自定义 API 地址下显式启用失效。

Fable 支持独立选择模型 ID、手填外部模型和“不设置”，参与一键设置及参数参考。`ANTHROPIC_DEFAULT_FABLE_MODEL` 控制 `fable` 别名，未设置时使用 Claude 的默认解析规则；字段首次支持版本未确认。配套的 `_NAME`、`_DESCRIPTION` 和 `_SUPPORTED_CAPABILITIES` 可通过原文编辑器配置，表单保留这些字段。依据：[官方模型配置](https://code.claude.com/docs/en/model-config#environment-variables)，核对日期 2026-11-20。

模型 ID 的 `[1m]` 和配置文件数值不能扩展上游真实能力。第三方模型的上下文、思考强度和工具搜索仍取决于上游兼容性。

### 2.2 新建文件预设

目标 Claude 配置文件不存在时，页面初始草稿使用以下值；已有文件按当前内容读取：

| 页面配置项 | 初始选择 | 保存字段 |
| --- | --- | --- |
| 命令沙箱 | 不设置 | 省略 `sandbox.enabled` |
| 自动记忆 | 禁用 | 顶层 `autoMemoryEnabled: false` |
| 提交与 PR 署名 | 禁用 | 顶层 `attribution: false` |
| Attribution Header | 禁用 | `env.CLAUDE_CODE_ATTRIBUTION_HEADER: "0"` |
| 非必要流量 | 禁用 | `env.CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: "1"` |
| 实验性 Agent Teams | 启用 | `env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS: "1"` |
| Tool Search | 启用 | `env.ENABLE_TOOL_SEARCH: "true"` |
| 隔离环境标记 | 启用 | `env.IS_SANDBOX: "1"` |
| 跳过网页抓取预检 | 启用 | 顶层 `skipWebFetchPreflight: true` |

默认目标为用户级 `~/.claude/settings.json`。本机提供 `CLAUDE_CONFIG_DIR` 时使用该目录下的 `settings.json`；SSH 目标位于远程用户主目录。自定义路径对应的作用域由 Claude Code 决定。

### 2.3 权限、沙箱与署名

权限模式写入 `permissions.defaultMode`，支持 `default`、`acceptEdits`、`plan`、`auto`、`dontAsk`、`bypassPermissions`。从 `2.1.257` 起，项目级和本地项目设置中的 `bypassPermissions` 不生效；`auto` 同样受作用域限制。命令行 `--permission-mode` 优先于文件默认值，组织策略仍可限制使用。

命令沙箱写入 `sandbox.enabled`，支持 macOS、Linux、WSL2，不支持原生 Windows。Linux / WSL2 需要 `bubblewrap`、`socat` 等依赖。`sandbox.enabled` 控制子命令沙箱，不代替 root 用户启动 `bypassPermissions` 时的隔离环境检查。

`attribution` 与 `env.CLAUDE_CODE_ATTRIBUTION_HEADER` 管理不同内容。“不设置”移除 `attribution`；“禁用”写入 `false` 并关闭提交、PR 和会话链接署名。旧版可使用对象兼容形式：

```json
{
  "attribution": {
    "commit": "",
    "pr": "",
    "sessionUrl": false
  }
}
```

`commit` 和 `pr` 的空字符串隐藏对应署名，`sessionUrl` 控制会话链接。旧字段 `includeCoAuthoredBy` 自 `2.0.62` 起弃用，当前仍支持读取。CLAUDE.md、记忆和组织策略也可能影响实际生成文本。

官方依据：[发布记录](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md)、[环境变量](https://code.claude.com/docs/en/env-vars)、[设置字段](https://code.claude.com/docs/en/settings-reference)、[权限模式](https://code.claude.com/docs/en/permission-modes)、[命令沙箱](https://code.claude.com/docs/en/sandboxing)、[Git 与 PR 署名](https://code.claude.com/docs/en/settings-reference#attribution)。

### 2.4 环境变量清除与当前会话

表单和原文编辑保存均写入目标 settings 文件。清除允许列表中的非空环境变量时，先写入空字符串 `""`，在该文件最后一次实际保存约 10 秒后移除仍为空字符串的对应字段。允许列表包含 API 地址与凭据、主模型与默认模型、子 Agent 模型、输出和上下文限制及部分协议开关，完整列表见 [`claude_env_cleanup.py`](../src/services/claude_env_cleanup.py)。其他字段直接按编辑结果保存；空字符串处理不适用于任意环境变量。

每个机器身份与文件路径只有一条待清理记录，由单个后台任务扫描。同一文件的连续实际保存合并字段并更新到期时间；无内容变化的保存保持原到期时间。重新设置的非空值退出清理范围，清理时保留外部新增的字段、注释与已修改的值。表单、原文编辑器、预览版本及备份使用删除后的逻辑内容，磁盘文件在等待期间包含临时空字符串。

待清理的路径、字段名与到期时间保存在 `agent-config/state.json`，不包含变量值。服务重启后继续扫描；SSH 文件需要对应机器的有效连接，离线时保留记录，连接恢复后继续处理。文件访问或解析失败时约 60 秒后重试。机器身份变化时放弃原身份的清理记录。

Claude Code 监听 settings 的变化。cc-switch 对 Claude Code `2.1.318` 的核对表明，`env` 热加载使用增量合并，直接删除文件字段可能让运行中进程保留旧值；允许列表中的空字符串用于撤销这些旧值。具体字段是否即时生效仍取决于 Claude 版本、配置作用域和会话状态，部分设置需要新会话。实现依据：[cc-switch 字段清单](https://github.com/farion1231/cc-switch/blob/main/src-tauri/src/live/project/claude.rs)、[直接模式写入](https://github.com/farion1231/cc-switch/blob/main/src-tauri/src/services/provider/claude_direct.rs)，核对日期 2026-11-20。

本页五项模型设置写入 `env`。`ANTHROPIC_MODEL` 参与启动时的主模型选择，运行中会话的模型选择优先于此变量；settings 的环境变量重载不保证切换当前主模型。顶层 `model` 同样在启动时读取。Haiku、Sonnet、Opus、Fable 变量指定别名对应的模型 ID，使用显式模型 ID 的会话不通过这些别名解析。终端使用 `/model`、VS Code 使用 **Switch model…** 切换当前会话；保存文件不调用这些会话操作，也不切换已发出的请求。依据：[设置生效时机](https://code.claude.com/docs/en/settings#when-edits-take-effect)。

本页属于配置控制平面。模型映射属于代理数据面：客户端发送稳定映射 ID 时，代理可根据当前映射选择不同上游，客户端显示的模型 ID 可以保持原值。

## 3. Codex 与 Opencode

### 3.1 Codex

API 服务配置、模型、思考强度、上下文窗口、压缩 Token 阈值、服务处理档位、沙箱范围、确认策略和 `features.memories` 的最低版本均未确认。官方字段列表不提供首次支持版本表，不能仅依据字段存在推断旧版兼容。

表单固定写入 `model_provider: proxy` 和 `model_providers.proxy`，模型 ID 保持完整的 Proxy 路由格式。

模型下拉支持“不设置”；外部服务地址下可清空模型输入。主动清空并保存时删除当前文件的 `model`，已有文件缺少该字段时保持“不设置”。新建文件且地址指向本服务时，初始草稿提供候选模型，支持手动选择“不设置”。未指定模型时由 Codex 的其他配置层或客户端默认值决定，实际请求使用的模型 ID 需要在目标服务可用。

页面同时管理 `~/.codex/.env`（本机尊重 `CODEX_HOME`），写入 `NO_PROXY` 代理绕过列表。Codex 启动时加载该文件并覆盖同名进程环境变量，`CODEX_` 前缀的变量不能通过此文件设置。Codex 由 Rust 实现，`NODE_TLS_REJECT_UNAUTHORIZED` 对它无效；自定义 CA 证书使用 `SSL_CERT_FILE` 等进程环境机制，不在本页管理范围。依据：[`.env` 加载实现](https://github.com/openai/codex/blob/main/codex-rs/arg0/src/lib.rs)，核对日期 2026-11-18。

依据：[官方配置参考](https://developers.openai.com/codex/config-reference)、[官方发布记录](https://developers.openai.com/codex/changelog)。

### 3.2 Opencode v1

| 配置项 | 当前写入格式 |
| --- | --- |
| API 地址、API Key、思考强度、上下文、最大输出 | V1 `provider / options` |
| 模型 | 顶层 `model`，与 provider 模型条目关联 |
| 压缩阈值 | `compaction.reserved` |
| 操作确认策略 | `permission` 字符串：allow / ask / deny；自定义对象未主动替换时保留 |
| 禁用服务 | `disabled_providers` 字符串数组，优先于 `enabled_providers` |
| 会话分享 | `share`：manual / auto / disabled |
| 更新检查 | `autoupdate`：false / true / notify；true 与 notify 均只检查并通知 |

表单固定写入 `provider.proxy`，顶层模型引用使用 `proxy/<完整 Proxy 模型 ID>`。

默认模型支持“不设置”，保存时删除顶层 `model`，保留连接、模型列表及模型级参数；已有文件缺少 `model` 时保持“不设置”。未选择默认模型时，本页不编辑模型级上下文、最大输出和思考强度；Opencode 根据启动参数、其他配置、最近使用或可用模型选择实际模型。

表单仅管理 V1 字段，原文件中的 V2 字段原样保留，历史登记的 V2 `permissions` 不参与自动清理。依据：[V1 模型](https://opencode.ai/v1/docs/models/)、[V1 配置](https://opencode.ai/v1/docs/config/)、[V1 权限](https://opencode.ai/v1/docs/permissions/)。

### 3.3 Opencode v2

- 默认配置为 `~/.config/opencode/opencode.json`，支持自定义 JSON / JSONC 路径。V1 与 V2 默认编辑同一文件，各自的自定义路径独立保存，页签不负责版本迁移。
- 表单固定使用 `providers.proxy` 和 `proxy/<模型别名>`；目标不存在时创建，已存在时写入本次表单字段。
- 按顶层 `model` 定位 `proxy` 中的当前模型，读取 `providers.proxy.settings`、模型 `settings` 与 `limit`；`modelID` 别名在未切换模型时保留。
- 默认模型支持“不设置”，仅移除顶层 `model`，保留模型别名、连接、模型列表和模型参数。未选择默认模型时，本页不编辑模型级容量和思考强度；实际模型由会话选择及客户端默认规则决定。
- 权限写入 `permissions: [{action: "*", resource: "*", effect: "allow|ask|deny"}]`。细分规则默认保留，主动选择策略时替换顶层数组，不设置删除该数组。
- 压缩预留对应 `compaction.buffer`，支持百分比字符串和正整数 Token 数，表示空余预算；`compaction.keep` 等字段保持原样。
- 文件快照使用 `snapshots`，更新策略使用全局 `update`，会话分享使用 `share`。
- `disabled_providers` 由 V2 规范化为 `provider.use` 禁用策略，原有 `policies` 保留。V1 的 `provider`、`permission`、`compaction.reserved` 等字段保持原样。

依据：[V2 配置](https://opencode.ai/docs/config/)、[V2 模型](https://opencode.ai/docs/models/)、[V2 服务](https://opencode.ai/docs/providers/)、[V2 权限](https://opencode.ai/docs/permissions/)、[官方 JSON Schema](https://opencode.ai/config.json)。核对日期：2026-11-13。

## 4. DSH 与 Hermes

### 4.1 DSH

- 默认全局补丁为 `~/.dsh/cordis.patch.yml`，凭据文件为 `~/.dsh/.credentials.yaml`，本机尊重 `DSH_HOME`。全局补丁在 Web、Desktop、headless、SDK、ACP 的 profile 补丁之后加载。
- 页面管理 `llm-pi-ai` 插件中的 `proxy` 路由、模型容量及 `agent-default-model` 默认选择，其他模型和插件保持原样。
- 默认模型支持“不设置”：移除当前补丁中 `agent-default-model` 的整组 `config` 覆盖（provider、model、reasoningEffort），保留连接、凭据及模型列表。下层 profile 或插件配置需提供有效的默认选择；未选默认模型时，本页不编辑模型容量与默认思考强度。
- 全局默认模型覆盖会阻止 DSH 将不同的默认选择保存到 profile，当前会话仍可切换模型。不设置全局默认模型后，默认选择可由 profile 管理。
- 思考强度提供“不设置”和 llm-pi-ai 支持的 off、minimal、low、medium、high、xhigh、max 档位；ultra 不属于该适配器的档位。候选不代表当前模型支持的档位；模型需在 DSH 中声明推理档位，未声明时使用“不设置”。reasoningEfforts 可映射各档位对应的 API 参数值，off 的实际效果由适配器与模型兼容配置决定。
- API Key 保存为凭据文件的 `refs.LLM_PROXY_API_KEY`，补丁仅保存 `apiKeyEnv` 引用；主动清空 Key 同时删除引用。
- 目标 Profile 需包含 `llm-pi-ai`、`agent-default-model` 及权限插件。sdk-minimal 的插件组合独立，启动参数、既有设置和会话选择可能覆盖默认值。
- 默认权限写入 `permission.config.defaultPreset`，支持 `read-only`、`workspace-write`、`danger-full-access`。never 表示拒绝仍需审批的请求，不自动批准；默认值仅作用于新会话。
- 标准权限预设保留其他自定义项；同名但不同语义的预设显示为自定义。选择不设置只移除 `defaultPreset`。
- 上下文和最大输出是模型容量声明，不等同每次请求的输出上限。重复受管插件补丁需要先在原文编辑器整理。

依据：[模型配置](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/providers.md)、[Profile](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/README.md#profiles)、[权限预设](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/interaction/permission-presets/README.md)、[模型适配器](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm-pi-ai/README.md)、[凭据文件](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/credentials/credentials-local/README.md)。核对日期：2026-11-13。

### 4.2 Hermes

- 默认路径为 `~/.hermes/config.yaml`，本机尊重 `HERMES_HOME`；其他 profile 使用自定义文件路径。
- 支持 API 地址、Key、默认模型、API 协议、服务类型、思考强度、上下文、压缩阈值、最大迭代次数、命令确认、长期记忆、用户画像及自动压缩。
- 默认模型支持“不设置”，仅删除 `model.default`，保留服务类型、地址、凭据及其他配置。模型可由启动参数或 Hermes 的默认规则提供；自定义服务需要可用的模型选择，留空不保证自动发现任意端点的模型。
- 新建配置的服务类型为 `custom`，协议为 `chat_completions`；Responses 写入 `codex_responses`，Anthropic Messages 写入 `anthropic_messages`。
- 服务类型支持自动选择、常用内置服务和手动输入；已有 `custom:<name>` 等命名服务原样保留。
- 思考强度支持“不设置”及 none、minimal、low、medium、high、xhigh、max、ultra。ultra 是 Hermes 内部档位，按模型与路由转换为支持的强度，不直接代表上游协议档位。
- 压缩阈值使用小数比例，`0.5` 表示 50%。命令确认使用 `smart`、`manual`、`off`。
- `model: ""` 或字符串模型简写可转换为可编辑映射；其他配置与 YAML 注释保留。

依据：[配置说明](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/configuration.md)、[模型配置](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/configuring-models.md)、[服务类型](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/integrations/providers.md)、[API 协议规范值](https://github.com/NousResearch/hermes-agent/blob/main/tests/hermes_cli/test_api_mode_aliases.py)。核对日期：2026-11-13。

## 5. 模型参数表维护

模型参数统一维护在 [`src/config/agent_model_limits.json`](../src/config/agent_model_limits.json)，Claude、Codex 和 Opencode 共用同一套匹配与候选数据。具体模型 ID、参数来源和核对日期只在这份 JSON 中维护。

顶层是 JSON 数组，每个对象代表一组参数相同的模型：

| 字段 | 维护规则 |
| --- | --- |
| `ids` | 至少一个小写基础 ID，省略 Provider 前缀和 `[1m]` 后缀 |
| `aliases` | 可选别名列表，在整份表中唯一 |
| `label` | 该组参数的来源说明 |
| `context` / `output` | 默认上下文与最大输出 Token，分别与对应候选第一项一致 |
| `compact` | 可选默认压缩百分比，与 `candidates.compact` 第一项一致 |
| `candidates.context` | 按推荐顺序排列的上下文候选 |
| `candidates.compact` | 1–100 的压缩比例候选，实际范围由 Agent 配置规则决定 |
| `candidates.output` | 模型支持或客户端适用的输出预算候选 |

每个候选包含正整数 `value`、来源类型 `kind`、短说明 `label`，以及可选的 `sourceUrl` 和 `checkedAt`。`kind` 支持 `official`、`client-default`、`derived`、`fallback`。官方数值应附资料地址和实际核对日期；规格不同的模型分别建立表项。

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

匹配顺序为模型 ID、别名、主 ID 的日期快照。匹配忽略大小写，移除 Provider 前缀和 `[1m]` 后缀，并支持常见点号与连字符写法。精确匹配优先于日期快照匹配。

Agent 参数表描述“模型名称对应什么参数”，后台模型映射描述“请求发给哪个上游”。固定指向同一规格模型的路由别名可加入 `aliases`；参数不同的多目标路由使用独立表项。未识别模型显示通用候选，已保存的 Agent 配置保留原值。

在仓库根目录执行校验：

```sh
uv run python scripts/check_agent_model_limits.py
```

校验覆盖 JSON 重复字段、模型名称冲突、数值类型、百分比范围、重复候选、来源字段格式和第一候选一致性。维护完成后重启 LLM_Proxy 并刷新 Agent 页面。

## 6. 扩展验收

检查新增和已有文件的读取回显、注释与未知字段保留、显式删除、重复保存幂等、自定义路径持久化、秘密预览打码、文件冲突、多文件回滚，以及本机和 SSH 的相同编辑计划。自定义文件路径、SSH 连接、凭据候选和一键设置属于 LLM Proxy 管理页功能，不具有独立的 Agent 最低版本要求。
