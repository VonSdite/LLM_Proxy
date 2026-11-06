# 前端迁移功能与验证记录

更新日期：2026-11-06。分支：`feat/vue-admin`。功能基线：`764496569a06d55922477035bc19d53d66f6fd32`（2.3.0）。范围见 [完整计划](frontend-vue3-migration-plan.md)。

## 交付状态

登录与全部八个后台页面使用 Vue 3、TypeScript、统一路由和共享控件。Flask 的已知页面路由使用 `templates/app.html`，生产资源位于 `src/presentation/static/app/`。旧业务模板、页面脚本、页面 CSS 和 Bootstrap 资源已清理，Ace 本地资源保留。

组件、后端、浏览器交互、生产入口与视觉验收均已执行。下表分别列出证据类型，浏览器 API 使用隔离 fixture，真实 SSH 与上游 OAuth 服务的验证边界见文末。

## 页面功能对应

源文件路径相对于 `frontend/src/`，前端测试路径相对于 `frontend/tests/`。

| 页面 / 领域 | 基线实现 | API 与能力 | Vue 实现 | 已执行验证 |
| --- | --- | --- | --- | --- |
| 公共外壳 | base_admin.html、theme.js | bootstrap、用户、版本、导航、退出、主题 | app/AppShell.vue、stores/session.ts、stores/theme.ts | Flask 入口/缓存；admin、layout 的深链接、刷新、历史导航和主题持久化；全部页面截图 |
| 登录 | login.html | POST /api/login、/api/logout；服务端跳转 | pages/LoginPage.vue、api/session.ts | admin 登录失败及 Enter 提交；forms 认证关闭和会话失效；api-client 与后端认证 |
| 设置 | settings.html | basic、client-ip、debug、oauth、api-keys、model-mapping 分组保存 | pages/SettingsPage.vue、features/settings/SettingSection.vue | forms 端口校验、分组保存、功能导航同步；接口回归；响应式截图 |
| Provider 管理 | providers.html | CRUD、复制、启停、排序、批量、JSON 导入导出 | pages/ProvidersPage.vue、features/providers/ProviderDialog.vue | forms 创建与错误字段；workflows 编辑、导入、导出、批量；后端 CRUD 与排序回归 |
| Provider 模型 | providers.html | 表单快照拉取与测试、整理、批量显隐、详情 | features/providers/ProviderModels.vue、ModelTestResult.vue | provider-models、permissions；workflows 嵌套选择、测试和保存；实际鼠标拖动后保存顺序 |
| Provider 配置和 Hook | providers.html | Provider 字段与相对 hooks/ 的路径 | features/providers/ProviderDialog.vue | 弹窗、下拉与长文本截图；表单请求及后端校验；基线 Hook 能力为路径输入 |
| Auth Group / Entry | providers.html | 嵌套条目、Headers、限制、YAML 导入、运行态、错误、启停与重置 | features/providers/AuthGroupDialog.vue、AuthRuntimeDialog.vue、auth-entry.ts | auth-entry 和后端回归；forms 嵌套条目与原始凭据提交；明暗与窄屏弹窗 |
| Codex OAuth 登录 | oauth.html | session、callback；账号与额度读取 | features/oauth/OAuthLoginDialog.vue、useOAuthFiles.ts | forms 授权 URL、回调失败保留弹窗；后端 OAuth 回归 |
| OAuth 模型目录 | oauth.html | 文本/图片模型增删、恢复内置、默认图片模型 | features/oauth/OAuthCatalog.vue | 目录实现与接口对应审查；后端目录接口回归；页面入口和主题截图 |
| OAuth 文件与额度 | oauth.html | 筛选分页、选择、批量启停删除、导入导出、额度、重置卡 | pages/OAuthPage.vue、features/oauth/OAuthFileCard.vue、useOAuthFiles.ts | oauth-files、oauth-page；workflows 刷新链、消费确认和导出；卡片截图 |
| 模型映射 | model_mappings.html | CRUD、策略、优先级、排序、启停、测试、导入导出、批量 | pages/ModelMappingsPage.vue、features/model-mappings | model-mapping、sortable-list；forms 完整 ID 与空行过滤；候选菜单及接口回归 |
| API Key | api_keys.html | 创建编辑、模型权限、配额、复制、启停、删除 | pages/ApiKeysPage.vue、features/api-keys/ApiKeyDialog.vue | permissions、后端回归；forms 模型权限与配额提交；字段与弹窗截图 |
| Agent 机器与文件 | agents.html、agents.js | /api/agent-config/*；本机、SSH、代理、指纹、路径 | pages/AgentsPage.vue、features/agents/useAgentWorkspace.ts、MachineDialog.vue、PathDialog.vue | 下方 20 项行为对应 |
| Agent 参数与运行设置 | agents.js | bootstrap、snapshot、preview、apply | features/agents/configuration.ts、AgentModelFields.vue、AgentParameters.vue、AgentRuntime.vue | 候选、精确值、三态、草稿、票据与实际浏览器输入、菜单、截图 |
| 用户 | users.html | CRUD、白名单、模型权限、批量、导入导出 | pages/UsersPage.vue、features/users | permissions 与后端回归；forms 失败重试；layout 加载/错误/空数据/千条分页与选择 |
| 统计与日志 | index.html | 四种视图、日期/用户/模型、排序、分页、导出和日统计导入 | pages/StatisticsPage.vue、features/statistics、api/statistics.ts | statistics、statistics-page；admin 日志页码与下载范围；keyboard 多选查询；后端及截图 |

OAuth 页面展示 Codex，Claude OAuth 后端接口保留。控制平面业务接口与数据平面沿用既有职责。

## Agent 20 项行为契约

编号对应计划第 8 节。`tests/test_agent_config.py` 验证配置语义、主机与浏览器会话隔离、路径、文件版本、备份及票据事务。下表中的组件测试位于 `tests/unit/`，浏览器测试位于 `tests/e2e/`。

| # | 实现与验证证据 |
| --- | --- |
| 1 | AgentsPage、useAgentWorkspace；agents 的本机默认、新建、选择、编辑、连接、断开与删除流程 |
| 2 | MachineDialog、后端 SSH 保护；agents 的 HTTP/HTTPS/SOCKS、密码/密钥、409 指纹确认与连接后编辑 |
| 3 | ConfigPathRow；agents 检查路径文字命中及行空白不打开文件；文件状态位于文件名前 |
| 4 | PathDialog、后端持久路径；agents 检查三种 Agent 自定义路径刷新保留及 Codex 双文件关联；forms 恢复默认 |
| 5 | configuration 端点识别；admin 外部地址手填与一键模型同步；agents 和截图覆盖本服务候选与同行布局 |
| 6 | ApiKeyField、UiCombobox；agents 聚焦、点击、移入与停留后候选仍显示，选择与手填保留实际值；抑制自动填充属性检查 |
| 7 | agent-configuration 的一键同步和混合值清空；admin 实际输入同步并保留焦点 |
| 8 | AgentModelFields、UiHelpPopover；agents 悬停、移入内容；keyboard 聚焦、Escape、描述关联；双列与窄屏截图 |
| 9 | Provider 优先级、多参考模型、别名、未知模型组件回归；agents 候选中的名称、来源和多模型数值 |
| 10 | AgentParameters 与公共数值输入；思考强度顺序、统一控件及参数浮层截图 |
| 11 | number-input 的失焦、相等值和候选提交；agents 的 Codex 压缩上限与 opencode 压缩预留标题更新 |
| 12 | agent-configuration 的精确 Token、空值、零值与显式移除；agents 保留原文件精确压缩值 |
| 13 | AgentRuntime、UiTriState；agents 三态选择和保存；四列、双列与单列截图 |
| 14 | agent-configuration、agent-workspace、后端默认值；agents 已有文件缺项、新建默认和命令沙箱状态；Codex memories 范围沿用基线 |
| 15 | agent-workspace 的迟到响应隔离；agents 连续输入后的预览 DOM、输入焦点、光标与滚动稳定 |
| 16 | ConfigFileEditor、UiCodeEditor；agents JSON/JSONC/TOML 高亮、备份、遮罩脏关闭与继续编辑；明暗 Ace 截图 |
| 17 | config-file-editor 与后端票据事务；agents 一次保存、冲突保留内容及重试 |
| 18 | agents 页面离开无额外确认；原始文件编辑器使用独立关闭钩子 |
| 19 | machine-dialog 与 agents 必填字段阻止请求；错误字段、图标与说明使用危险色；SSH 错误截图 |
| 20 | admin 所有页面主题切换与刷新保留；页面、候选、帮助、机器弹窗和编辑器截图 |

## 测试迁移记录

基线有 799 项 Python 测试，另有 `agents_editor.test.cjs` 39 项和 `model_mapping_editor.test.cjs` 13 项 Node 用例。Node 用例由 Python 包装调用，数量不与 Python 测试简单相加。

旧模板字符串检查和旧脚本专用 DOM 桩已有组件、接口或浏览器层的行为对应。当前 769 项 Python 回归覆盖 API、配置、数据平面、认证、SSH、OAuth、映射和统计；`test_ui_entry.py` 覆盖 Vue 入口与缓存。数量本身不作为行为等价证据。

| 旧用例族 | 当前证据 |
| --- | --- |
| Agent 模型、端点、三态、默认值、参数 | agent-configuration、number-input、combobox；agents/admin E2E |
| Agent 预览、保存、迟到响应、冲突 | agent-workspace、request-order、后端 Agent；agents E2E |
| 文件编辑、备份、脏关闭 | config-file-editor、dialog、后端文件；Ace 与遮罩关闭 E2E |
| SSH 字段、自定义路径 | machine-dialog、path-dialog、后端机器/路径；agents/forms E2E |
| 映射替换、测试、凭据、未知指标、迟到结果 | model-mapping、sortable-list；forms 与浮层截图 |
| Provider 拉取/整理/测试/排序、候选 | provider-models、permissions、table；workflows、键盘列宽及实际拖动 |
| OAuth 配额/重置卡/刷新/选择 | oauth-files、oauth-page、后端 OAuth；workflows 与卡片截图 |
| 默认日期、分页、Key 用量、统计 | statistics、statistics-page、后端统计；admin/keyboard E2E |
| 控件、版本、列宽、提示 | ui_entry、共享组件、展示页；keyboard/layout 与视觉基线 |

## 共享组件与维护

基础控件统一从 `@/components/ui` 导入；Reka UI、TanStack Table 和图标的直接导入限定在包装层。固定选择与搜索选择提供 `option` 插槽。表格和目标列表复用拖动逻辑，并提供上下移动按钮；真实浏览器检查包含原生拖放与保存后的顺序。展示页 `/dev/components` 使用正式组件，仅在开发和测试模式可达。

业务复用包含模型权限、模型候选、API Key、代理字段、路径行、文件编辑器和 JSON 导入。请求层负责同源 Cookie、Agent 请求头、上传下载、取消与会话错误分类；读取和操作组合函数负责迟到响应、重复提交及卸载清理。取消客户端请求不回滚已在服务器提交的写入。

Portal 浮层与通知的样式使用唯一类名匹配实际 DOM；字段标签和帮助说明关联原生元素 ID。桌面保留紧凑尺寸，粗指针设备上的常用控件使用至少 44px 点击区域。维护方式见 [开发指南](frontend-development.md) 与 [frontend/AGENTS.md](../frontend/AGENTS.md)。

## 已执行检查

| 检查 | 结果 |
| --- | --- |
| `npm ci` | 工作区与独立目录按锁文件安装通过 |
| `npm run lint` | ESLint、Stylelint、Prettier 通过 |
| `npm run typecheck` | 应用、构建配置和测试类型通过 |
| `npm run test:unit` | 86 项 / 22 文件通过 |
| `npm run build` | 生产构建通过，按路由拆分，Ace 本地按需加载 |
| `npm run test:e2e` | 44 个用例均通过；最终完整轮 43 项通过，布局用例的等待条件调整后 4 项定向复跑通过 |
| `npm run test:visual` | 134 项截图比对通过，包含 80 个页面、52 个浮层、2 个触屏截图 |
| Python 全量回归 | 769 项通过；最终生产构建后入口/Agent 56 项再次通过 |
| Python 格式 | 修改文件 Ruff import、format 与失效 import 清理通过 |
| 模型参数表 | 65 组、92 个模型 ID、12 个别名通过 |
| 独立环境构建 | 独立 npm/uv 环境安装通过；最终源码重新构建，31 个产物 SHA-256 与工作区相同 |
| Flask 生产 HTTP | 9 个后台路径、31 个产物内容与缓存通过；未知 API、数据路径及展示页为 404 |
| Flask 生产浏览器 | 登录、后台及 /providers 别名共 10 个入口实际加载哈希资源；主题和 Ace 正常；无 JavaScript 异常 |
| Vite 开发代理 HTTP | Origin / Host、Cookie、X-Agent-Config 保留；缺请求头与跨域返回 403，跨浏览器票据返回 409 |
| 文字对比度 | 每个主题 16 组语义文字/背景配对均达到 4.5:1；深色最低 5.38，浅色最低 5.11 |
| 文本与补丁 | UTF-8 无 BOM、LF 与 `git diff --check` 检查通过 |

最终 manifest SHA-256：`8c653a98ed1775911fd559332cc6cfb24fe701c278d33948527310bb6accb8f7`。独立验证环境采用单独安装的依赖与 Python 环境；生产浏览器使用 Flask 提供真实构建资源和隔离 API 数据。生产运行不依赖 Node.js。

依赖锁定 Node.js 24.13.0 / npm 11.6.2 对应的兼容组合。图标使用 `@lucide/vue` 1.69.0，统一入口为 UiIcon。2026-11-06 的 npm 审计包含开发工具链 glob/minimatch 及依赖链共 14 项中等级提示，无高/严重项；生产依赖审计为 0 项。

## 浏览器与视觉证据

浏览器为用户指定 profile 的 Chrome 144.0.7559.60，经 `http://127.0.0.1:9222` CDP 连接。每个用例使用独立上下文，不读取用户 profile 的 Cookie、密码或其他存储。语言为中文，时区为上海，启用减少动画偏好。

截图与视觉复核覆盖登录、八个后台页面和组件展示页，在深浅主题及 1920 / 1440 / 1024 / 390px 下检查。52 个浮层截图覆盖 SSH 字段错误、API Key、模型与参数菜单、帮助、JSONC/TOML 编辑器、Provider、映射、Key、Auth Group、统计筛选和通知；触屏另有两张共享组件基线。布局检查包含文本换行、内部滚动、菜单层级、输入对齐、禁用与错误状态、无页面横向溢出。

基线目录：

- `frontend/tests/visual/pages.spec.ts-snapshots/`
- `frontend/tests/visual/overlays.spec.ts-snapshots/`
- `frontend/tests/visual/touch.spec.ts-snapshots/`

交互覆盖 Tab、Enter、Space、方向键、Escape、焦点约束与恢复、字段说明关联、列宽持久化、输入位置、请求迟到与草稿保留。加载/错误/重试/空数据和千条用户数据的服务器分页由 layout 用例覆盖。测试自动收集并断言浏览器 JavaScript 异常为空。

## 验证边界

- 浏览器业务接口使用受控响应，真实 SSH 服务器、第三方 OAuth 登录及真实上游模型调用不在本次浏览器验收范围；后端配置与协议契约由 Python 回归覆盖。
- 自动填充验证覆盖现有抑制属性与新建浏览器上下文中的操作行为，不代表所有密码管理器扩展和已保存凭据组合。
- 截图基线对应 Windows 与上述 Chrome 版本；其他操作系统、字体和浏览器版本需重新复核基线。
- 独立验证副本保留在系统临时目录 `llm-proxy-vue-check-9e1d51b31c234ae4b44a3b6a81c85363`，临时复核图片保留在已被 Git 忽略的 `frontend/.review-artifacts/`。两项清理操作均由自动审批返回 `blocked by policy`，未重试删除；验证服务已退出。

## 完成清单

- [x] 全页面 Vue 源码与 Flask 生产入口
- [x] 共享组件、正式组件展示页与请求层
- [x] 旧业务模板、脚本、样式与 Bootstrap 清理
- [x] 组件测试、Python 回归和模型表校验
- [x] 最终构建资源、独立环境安装与产物一致性
- [x] 全页面主要流程与 Agent 20 项行为验收
- [x] 明暗主题、键盘、浮层、响应式与长文本视觉验收
- [x] 开发约束、维护说明、架构与实际结果对应

架构变化为 Vue 管理界面、启动上下文和构建资源装配；控制平面 API 与数据平面业务边界保持原有职责。详见 [4+1 架构](architecture-4plus1.md)。
