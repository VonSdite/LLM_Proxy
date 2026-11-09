# Claude 配置兼容性

核对日期：2026-11-05。本机核对版本：Claude Code `2.1.310`。以下结论来自当前官方文档及本机安装程序；SSH 目标机器的实际行为取决于其 Claude Code 版本、配置作用域和上游服务。

`attribution` 文档核对日期：2026-11-07。该项依据官方设置参考及本项目配置文件读写测试，未运行实际 Git 提交或 PR 创建会话。

## 表单字段

| 配置项 | 当前行为 | 页面管理方式 |
| --- | --- | --- |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL`、`ANTHROPIC_DEFAULT_OPUS_MODEL`、`ANTHROPIC_DEFAULT_SONNET_MODEL`、`ANTHROPIC_MODEL` | 官方支持的模型映射和默认模型 | 四项独立编辑；“一键设置模型”在选择或输入时即时同步，单项不一致时顶部留空 |
| `ANTHROPIC_REASONING_MODEL` | 官方环境变量文档未列出，本机 `2.1.310` 程序也未发现该变量；没有当前有效的支持依据，不能据此断言它曾是官方接口或已有正式弃用公告 | 常规模型表单仅管理上述四项；历史字段可在文件编辑器查看 |
| `IS_SANDBOX` | 当前程序仍使用它识别运行环境；值为 `1` 时参与 Linux/macOS root 用户启动 `bypassPermissions` 的隔离条件判断，本身不创建沙箱 | “隔离环境标记”独立管理 `env.IS_SANDBOX`，启用写 `1`、禁用写 `0`；“命令沙箱”管理 `sandbox.enabled` |
| `CLAUDE_CODE_ATTRIBUTION_HEADER` | 仍有效；`0` 通常省略系统提示开头的客户端版本和提示指纹标识，部分直连自动审核请求仍可保留 | 启用写 `1`，禁用写 `0` |
| `attribution` | `settings.json` 顶层配置，控制 Git 提交、PR 描述及会话链接署名；支持对象或 `false`，`true` 无效 | 默认不设置；禁用写 `false`，需要 Claude Code `2.1.281+`；已有对象显示“文件自定义”，编辑其他项时原样保留 |
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | 任意非空值均禁用，包括字符串 `0`、`false`；涉及自动更新、遥测、错误报告和功能开关获取等 | 禁用写 `1`，允许时显式移除当前文件中的变量；进程环境中的值仍可能生效 |
| `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` | 仍有效，实验功能默认关闭 | 启用写 `1`，禁用写 `0` |
| `ENABLE_TOOL_SEARCH` | 仍有效；需要模型与网关支持 MCP 工具搜索。支持 `true`、`false`、`auto`、`auto:N` | 控件设置 `true` / `false`；已有自动阈值在未操作该控件时保留 |
| `skipWebFetchPreflight` | 官方支持的顶层布尔字段；`true` 跳过 WebFetch 抓取前发往 `api.anthropic.com` 的域名安全检查，WebFetch 访问权限与 WebSearch 分别受各自规则控制 | “关闭 WebFetch 预检”启用写 `true`、禁用写 `false`、不设置移除字段 |
| `CLAUDE_CODE_EFFORT_LEVEL` | 仍有效；模型决定可用档位，优先于 `/effort` 和启动参数；`auto` / `unset` 表示由 API 决定 | 支持五档强度及 `auto`；文件中的 `unset` 按等价的 `auto` 展示 |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | 自动压缩窗口，范围 `100000`–`1000000`；小于下限会忽略，大于上限按 100 万处理，且受模型实际窗口限制 | 显示为“压缩窗口”，保留文件原值并提供适用范围说明 |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | 接受 `1`–`100`，仅能提前压缩；高于默认阈值时被忽略，适用于上下文耗尽前执行自动压缩的会话 | 原值保存，说明中包含适用条件 |
| `CLAUDE_CODE_MAX_OUTPUT_TOKENS` | 仍有效，默认值和上限随模型变化；无法识别的模型默认 32000、上限 128000 | 按填写值保存，Claude Code 按模型上限处理；压缩窗口与单次输出预算独立 |

截图中的 `1048576` 超过当前自动压缩窗口上限，Claude Code 按最高 `1000000` 处理。模型 ID 上的 `[1m]` 和上述配置均不能扩展上游模型的真实能力；第三方模型的上下文、思考强度和工具搜索仍取决于上游兼容性。

## 新建配置的默认值

目标机器的 Claude 配置文件不存在时，页面初始草稿使用下列值。本机 Windows、Linux 和 SSH 机器使用同一组默认值，用户可在保存前调整。已有文件按当前字段值读取，包括空文件、空对象和缺少部分字段的文件。

| 页面配置项 | 初始选择 | 保存字段 |
| --- | --- | --- |
| 命令沙箱 | 不设置 | 省略 `sandbox.enabled` |
| Attribution | 不设置 | 省略顶层 `attribution` |
| Attribution Header | 禁用 | `env.CLAUDE_CODE_ATTRIBUTION_HEADER: "0"` |
| 非必要流量 | 禁用 | `env.CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: "1"` |
| 实验性 Agent Teams | 启用 | `env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS: "1"` |
| Tool Search | 启用 | `env.ENABLE_TOOL_SEARCH: "true"` |
| 隔离环境标记 | 启用 | `env.IS_SANDBOX: "1"` |
| 关闭 WebFetch 预检 | 启用 | 顶层 `skipWebFetchPreflight: true` |

`skipWebFetchPreflight` 位于用户级 `~/.claude/settings.json` 的顶层，Windows 默认对应 `%USERPROFILE%\.claude\settings.json`。本服务运行环境提供 `CLAUDE_CONFIG_DIR` 时，本机目标为该目录下的 `settings.json`；SSH 目标位于远程用户的主目录。官方同样支持在项目级 `.claude/settings.json`、`.claude/settings.local.json` 和管理级设置中使用此字段。本页的读取和保存预览展示实际目标文件，文件在点击保存后创建。

## Git 与 PR 署名

`attribution` 位于 Claude Code `settings.json` 顶层，可用于用户、项目、本地项目和管理级设置。默认未设置，Claude 按默认规则为 Git 提交添加 `Co-Authored-By` 等署名，为 PR 描述添加文本署名。它与 `env.CLAUDE_CODE_ATTRIBUTION_HEADER` 分别管理不同内容。

页面提供“不设置”和“禁用”。“不设置”显式移除当前文件中的 `attribution`，其他作用域或历史 `includeCoAuthoredBy` 仍可能影响实际署名。“禁用”写入 `attribution: false`，关闭全部署名，包括会话链接。`false` 从 Claude Code `2.1.281` 起支持，较旧版本可能将整个设置文件判为无效；旧版本可通过文件编辑器使用以下兼容对象：

```json
{
  "attribution": {
    "commit": "",
    "pr": "",
    "sessionUrl": false
  }
}
```

对象中的 `commit` 和 `pr` 分别设置提交与 PR 的自定义署名，空字符串隐藏对应署名，缺省项沿用默认值；`sessionUrl` 控制会话链接。已有对象在页面显示“文件自定义”，内容、注释和额外字段在修改其他配置时保持原样，具体文本可在配置文件编辑器维护。将该项切换为“禁用”或“不设置”会替换或移除整个 `attribution` 字段，其他设置保持不变。

旧字段 `includeCoAuthoredBy` 自 `2.0.62` 起弃用，当前仍支持读取。设置 `attribution.commit` 或 `attribution.pr` 后，`includeCoAuthoredBy` 不再控制署名。CLAUDE.md、记忆和组织管理策略也可能影响实际生成的提交或 PR 文本。

## 权限模式与命令沙箱

权限模式控件写入 `permissions.defaultMode`，作用于新会话。页面提供：

| 值 | 行为 |
| --- | --- |
| `default` | 需要时人工确认；已有 `manual` 别名按此项展示 |
| `acceptEdits` | 自动接受文件编辑及部分文件系统操作 |
| `plan` | 分析和规划，批准计划后执行 |
| `auto` | 后台分类器审核操作，依赖目标版本与服务支持，并可能增加请求 |
| `dontAsk` | 仅执行已授权或无需确认的操作，需要确认的操作通常被拒绝 |
| `bypassPermissions` | 跳过权限确认，拒绝规则及部分强制检查仍适用 |

从 `v2.1.257` 起，项目级 `.claude/settings.json` 与 `.claude/settings.local.json` 中的 `bypassPermissions` 不生效；当前 `auto` 同样受作用域限制。这两个值适用于用户级 `~/.claude/settings.json` 或允许的管理级配置。页面默认编辑用户级文件，自定义路径所代表的作用域由 Claude Code 决定。命令行 `--permission-mode` 优先于文件中的默认模式，组织策略仍可限制模式使用。

命令沙箱控件写入 `sandbox.enabled` 布尔值。支持 macOS、Linux、WSL2；原生 Windows 不支持。Linux / WSL2 需要 `bubblewrap`、`socat` 等依赖。沙箱不可用时的行为由已有的 `sandbox.failIfUnavailable` 等设置决定。

Linux/macOS 的 root 用户在未被识别为隔离环境时，启动 `bypassPermissions` 仍会收到 `--dangerously-skip-permissions cannot be used with root/sudo privileges for security reasons`。本机 `2.1.310` 安装程序中的对应条件为：平台不是 Windows、UID 为 0、`IS_SANDBOX` 不等于字符串 `1`、同时没有 Bubblewrap 环境标记。`sandbox.enabled` 控制子命令沙箱，不代替这个启动环境条件。普通用户和原生 Windows 不需要 `IS_SANDBOX` 来通过该项检查。

模式和沙箱设置通过现有读取、保存预览、冲突校验和备份流程生效。修改默认模式保留 `permissions.allow`、`ask`、`deny`、`disableBypassPermissionsMode` 等其他配置；修改沙箱开关保留其网络与文件系统规则。选择“不设置”时，显式删除对应字段。

已有手工配置的历史变量保留在文件内；本页此前管理且未被外部修改的历史字段，继续按既有字段所有权规则在保存预览中显示清理操作。所有实际文件写入均发生在用户点击保存之后。架构未变化。

## 核对来源

- [官方环境变量参考](https://code.claude.com/docs/en/env-vars)
- [权限默认模式参考](https://code.claude.com/docs/en/settings-reference#permissions-defaultmode)
- [权限模式](https://code.claude.com/docs/en/permission-modes)
- [自动压缩窗口](https://code.claude.com/docs/en/model-config#set-the-auto-compact-window)
- [命令沙箱](https://code.claude.com/docs/en/sandboxing)
- [WebFetch 预检设置](https://code.claude.com/docs/en/settings-reference#skipwebfetchpreflight)
- [WebFetch 域名安全检查](https://code.claude.com/docs/en/data-usage#webfetch-domain-safety-check)
- [Git 与 PR 署名设置](https://code.claude.com/docs/en/settings-reference#attribution)
- 本机 `claude --version`、`claude --help` 与安装包静态变量检查，未运行模型会话。
