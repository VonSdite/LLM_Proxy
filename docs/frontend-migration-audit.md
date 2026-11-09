# 前端迁移功能与验证记录

更新日期：2026-11-06。分支：`feat/vue-admin`。功能基线：`764496569a06d55922477035bc19d53d66f6fd32`（2.3.0）。范围见 [完整计划](frontend-vue3-migration-plan.md)。

## 交付状态

登录与全部八个后台页面使用 Vue 3、TypeScript、统一路由和共享控件。Flask 的已知页面路由使用 `templates/app.html`，生产资源位于 `src/presentation/static/app/`。旧业务模板、页面脚本、页面 CSS 和 Bootstrap 资源已清理，Ace 本地资源保留。

组件、后端、浏览器交互、生产入口与视觉验收均已执行。下表分别列出证据类型，浏览器 API 使用隔离 fixture，真实 SSH 与上游 OAuth 服务的验证边界见文末。

## 页面功能对应

源文件路径相对于 `frontend/src/`，前端测试路径相对于 `frontend/tests/`。

| 页面 / 领域 | 基线实现 | API 与能力 | Vue 实现 | 已执行验证 |
| --- | --- | --- | --- | --- |
| 公共外壳 | base_admin.html、theme.js | bootstrap、用户、版本、导航、退出、主题 | app/AppShell.vue、app/AppNavigation.vue、stores/session.ts、stores/theme.ts | Flask 入口/缓存；admin、layout、navigation 的深链接、历史导航、主题、侧栏和退出；全部页面截图 |
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

本节记录 Vue 迁移基线 `ae93c71` 的验证结果；后续视觉样式与侧栏布局的验证结果见“全站视觉复核”和“侧栏布局验收”。

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

迁移基线 manifest SHA-256：`8c653a98ed1775911fd559332cc6cfb24fe701c278d33948527310bb6accb8f7`。独立验证环境采用单独安装的依赖与 Python 环境；生产浏览器使用 Flask 提供真实构建资源和隔离 API 数据。生产运行不依赖 Node.js。

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

## 全站视觉复核（2026-11-06）

登录及八个后台页面使用统一的蓝灰语义色、中文优先字体、柔和分隔线和表面阴影。表单标签、常规输入和表格正文为 14px；控件圆角为 10px，分组圆角为 14px。登录表单宽度上限为 420px，标题左对齐，标题区与输入区具有独立层次。导航、设置目录和 Agent 侧栏共享响应式顶部避让间距。

列表操作按钮横向排列，操作列具有最小宽度。Provider 地址按路径片段换行并保留完整文本；模型数量与模型名称并排。统计数字及对应表头右对齐。6 条 Provider、8 条用户记录的补充浏览器复核覆盖深浅主题与 1440 / 390px；双模型 Provider 行高约 67px，页面无横向溢出。设置锚点在 1440 / 1024 / 390px 下位于固定导航下方。

| 检查 | 当前结果 |
| --- | --- |
| 静态检查与构建 | ESLint、Stylelint、Prettier、TypeScript 和生产构建通过 |
| 单元测试 | 86 项 / 22 文件通过 |
| 浏览器交互 | 完整轮 42 项通过；另外 2 项采用当前浮层色值后定向复跑通过，共覆盖 44 项 |
| 视觉检查 | 134 张基线更新，最终 134 项截图比对通过；覆盖 80 个页面、52 个浮层和 2 个触屏场景 |
| Flask 入口 | `uv run python -m unittest discover -s tests -p test_ui_entry.py -q`，7 项通过 |
| 文字对比度 | 每个主题 16 组语义文字/背景配对均达到 4.5:1；深色最低 4.76，浅色最低 4.86 |

登录标题区仅显示“登录”，上下内边距为 20px。当前标题版式通过深浅主题、四种宽度的 8 项视觉检查与登录交互回归；对应截图基线、静态检查和生产构建均已更新。

登录标题版式的生产 manifest SHA-256：`c5b7a3d3d5f262de654dade10d29d558c5f6e362d706daa982b97a88e8f0da9e`。构建产物位于 `src/presentation/static/app/`，运行方式仍为 Python 启动。浏览器验证使用隔离的模拟业务数据；全站截图和多行列表复核提供视觉检查证据，审美确认以实际使用反馈为准。

架构未变化。当前变更范围为公共视觉样式、页面排版、表格列宽约束、视觉基线及生产资源。

## 侧栏布局验收（2026-11-06）

桌面主导航位于左侧，宽度为 216px，收起后为 72px。顶部提供收起按钮；底部账户入口与主题按钮共用一行，页面滚动时保持可见。账户入口显示用户名首字母和用户名，退出操作位于向上展开的菜单中。导航区域独立滚动。900px 及以下使用左侧抽屉，支持 Escape、遮罩关闭、切换页面后关闭和焦点恢复。桌面侧栏与抽屉共用 `AppNavigation.vue`，导航项沿用原有功能开关；操作菜单共用 `UiDropdownMenu`。

Agent 机器列表使用内容宽度为 980px 的容器断点，较窄时位于配置区域上方。页面内容随侧栏展开和收起调整宽度；宽表格保留内部横向滚动。截图复核覆盖展开侧栏、收起侧栏、手机抽屉，以及八个后台页面的深浅主题和 1920 / 1440 / 1024 / 390px 布局。

| 检查 | 结果 |
| --- | --- |
| 静态检查与构建 | ESLint、Stylelint、Prettier、TypeScript 和生产构建通过 |
| 单元测试 | 86 项 / 22 文件通过 |
| 浏览器交互 | 全站功能 44 项与账户、导航 4 项回归通过，共 48 项；包含长用户名、键盘与外部关闭、手机嵌套菜单、焦点恢复及退出失败重试 |
| 视觉检查 | 142 项视觉场景和基线更新通过，覆盖 80 个页面、60 个浮层和 2 个触屏场景；代表性截图完成复核，账户菜单、收起侧栏、手机抽屉及 OAuth 页面 16 项截图比对通过 |
| Flask 入口 | `uv run python -m unittest discover -s tests -p test_ui_entry.py -q`，7 项通过 |
| 生产资源 | manifest 引用和 31 个产物检查通过；文本为 UTF-8 无 BOM、LF |

当前生产 manifest SHA-256：`8b135c7e5d675164861e04230a5abc398b8040baffad3e8e260dc5961f49d41a`。产物位于 `src/presentation/static/app/`，生产运行仍通过 Python 启动。浏览器使用隔离的模拟业务数据。

架构未变化。当前变更范围为公共导航、抽屉布局、Agent 响应式排列、浏览器回归、视觉基线及生产资源。

## 页面布局与操作反馈验收（2026-11-07）

后台内容使用侧栏之外的可用宽度，桌面内边距为 24px。共享表格通过容器宽度按比例分配信息列空间，顺序、勾选、开关和操作列保持紧凑。Provider 的默认列宽在 1440px 下完整展示启停与操作，2560px 下地址与模型列获得更多空间。窄屏保留表格内部滚动，横向标签采用共享紧凑样式。

Provider、模型映射、用户、API Key、OAuth 与认证组运行状态使用共享开关等待反馈。等待期间保留行节点、列结构、选择与焦点，失败恢复已确认值。单项写入直接合并对应记录，已确认写入优先于在途旧读取。映射及其目标共用记录锁，其他记录独立提交。筛选行在保存成功后按当前条件显示；分页、排序和搜索变化清空列表选择。

系统设置使用公共横向标签与通栏面板。分组草稿在标签切换期间保留，保存按钮跟随变更状态，保存响应只更新对应分组。当前保存分组的字段禁用，其他分组保持可编辑；保存错误显示在当前分组内。存在未保存内容时，重新读取提供继续编辑和丢弃确认。

浏览器使用隔离 API 数据与用户指定的 Chrome CDP 实例。截图覆盖登录、八个后台页面和共享组件，在 2560 / 1920 / 1440 / 1024 / 390px 及深浅主题下检查；访问控制、OAuth 网络和日志设置另有 18 个分组截图。6 行 Provider 的补充截图覆盖 2560 / 1440 / 390px，检查长地址、长模型 ID 和操作排列。

| 检查 | 结果 |
| --- | --- |
| 依赖与静态检查 | `npm ci`、ESLint、Stylelint、Prettier、TypeScript 通过 |
| 单元测试 | 92 项 / 23 文件通过 |
| 浏览器交互 | 60 个用例覆盖通过；完整轮 59 项，最终表格调整的 19 项与 Provider 布局的 12 项定向回归通过 |
| 视觉检查 | 最终完整轮 180 项截图比对通过：100 个页面、60 个浮层、18 个设置分组、2 个触屏场景 |
| Flask 入口 | `uv run python -m unittest discover -s tests -p test_ui_entry.py -q`，7 项通过 |
| 生产浏览器 | Flask 提供实际构建资源，8 个后台入口加载 28 个哈希资源；局部开关、设置标签和主题切换通过，无页面异常及横向溢出 |
| 生产资源 | 30 个 manifest 资源引用及 32 个产物检查通过，生产构建包含共享 `useToggle` 分块 |
| 文本与补丁 | UTF-8 无 BOM、LF、`git diff --check` 通过 |

当前生产 manifest SHA-256：`2fd3e1fa8fe6e3649aff4ad90191766ecdda077b455c7ef05473382044f32e2b`。资源位于 `src/presentation/static/app/`，部署继续使用 Python 启动。浏览器数据为受控 fixture，截图复核图位于被 Git 忽略的 `frontend/.review-artifacts/layout-refinement/`；本次验证服务器已退出。

前端页面状态的职责记录在 [4+1 架构](architecture-4plus1.md)；后端分层、控制平面接口、数据平面与部署拓扑保持既有职责。

## 响应式侧栏验收（2026-11-07）

901–1280px 自动显示 72px 图标侧栏，支持手动展开；区间内缩放和切换页面保留当前选择，再次进入该区间时自动收起。1280px 以上恢复宽屏的手动选择，初始为展开。900px 及以下使用抽屉，回到桌面尺寸时关闭抽屉。侧栏状态属于 `AppShell`，不写入本地存储；断点切换保留业务页面节点与表单草稿。

| 检查 | 结果 |
| --- | --- |
| 静态检查与构建 | ESLint、Stylelint、Prettier、TypeScript、生产构建通过 |
| 单元测试 | 92 项 / 23 文件通过 |
| 浏览器交互 | 全部 62 项通过；覆盖 900 / 901 / 1280 / 1281px 边界、首次窄屏进入、手动展开、宽屏选择恢复、草稿与节点保留、抽屉关闭 |
| 视觉检查 | 全部 180 项截图比对通过；1024px 下 18 张后台及组件截图使用自动收起布局，Provider、用户、设置和 Agent 代表截图完成复核 |
| Flask 与生产资源 | 页面入口 7 项通过；30 个 manifest 资源引用和 32 个产物检查通过 |
| 文本与补丁 | UTF-8 无 BOM、LF、`git diff --check` 通过 |

当前生产 manifest SHA-256：`890ab34fed20e9e2d34e0d0dc25cd337f627fb906a4bab91fc5aa99773a4a187`。生产资源位于 `src/presentation/static/app/`，部署继续通过 Python 启动。浏览器使用隔离 API 数据与用户指定的 Chrome CDP 实例。

架构未变化。侧栏状态与监听生命周期记录在 [4+1 架构](architecture-4plus1.md)，布局规则记录在 [前端开发与验收](frontend-development.md)。

## 帮助说明复核（2026-11-07）

核对基线为 Vue 迁移前的 `7644965`，包括 Provider、用户、映射、OAuth、系统设置模板和 `agents.js` 的帮助内容。当前说明以现有服务、仓储和配置处理逻辑为准；客户端相关语义同时参考 [Claude Code 2.1.310 兼容性记录](claude-config-compatibility.md) 及 [Codex 配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)。

| 页面与区域 | 说明覆盖 |
| --- | --- |
| Provider 与模型编辑 | 路由前缀、完整请求端点、协议示例、上游凭据与认证组、Hook、代理来源、超时、尝试次数、证书、强制流式、脱敏、目录隐藏、拉取/整理/测试与指标 |
| Auth Group 与运行状态 | 选择策略、条目 ID、认证请求头、并发、429 冷却顺序、自然分钟/日配额、失败请求计数、Token 单位、配置启停与运行状态、重置影响 |
| 模型映射 | 对外 ID、优先级、粘滞策略、无正常目标时的兜底、不同错误的冷却/禁用行为、不可用目标、脱敏叠加 |
| 用户与 API Key | IP 身份、白名单默认值、权限交集、全部与指定模型、筛选选择范围、批量权限替换、累计用量、Key 累计配额与在途请求 |
| OAuth | 状态判定、自动同步与手动刷新、费用估算、重置卡、模型目录及图片默认值、完整回调 URL、文件导入覆盖、导出与删除归档 |
| Agent 配置 | 本机与 SSH 的作用机器、服务地址与回环地址、API Key、四模型同步、`[1M]`、模型参考来源、压缩计算、输出预算、Claude 与 Codex 运行选项 |
| 文件与机器弹窗 | 目标机器的路径、路径记忆与 Agent 实际加载的区别、备份顺序与读取、SSH 凭据来源、代理协议/域名解析/CONNECT、主机指纹 |
| 统计与日志 | 查询日期范围、查询后导出、模型流向、缓存统计分母与加权、耗时、请求明细去重与日聚合累加 |
| 系统设置 | 监听与重启、登录校验与清空密码、转发 IP 信任范围、API Key 与模型映射开关、OAuth 网络、日志目录/级别及完整请求调试日志 |
| JSON 导入与批量操作 | Provider 重命名与认证条目合并、同 IP 用户更新、映射 ID 冲突、统计日聚合重复累加、批量权限替换 |
| 登录与导航 | 直接操作名称、字段校验与已有简短提示；帮助入口集中在需要解释的业务字段 |

内容明确区分目录隐藏和调用权限、累计配额和周期配额、权限确认和命令沙箱。认证条目“重置”说明包含当前分钟与当天用量。Codex 的 `untrusted` 选项标注为旧版本，并说明当前文档的支持情况；`service_tier` 与 `default_permissions` 的版本差异在对应问号内说明，配置写入方式保持原有行为。

公共帮助采用分段和列表，桌面悬停、键盘聚焦与触屏点按均可阅读。表头问号独立于排序按钮，长内容在弹层内部滚动。业务内容放在字段、表头或操作区域的问号中，页面保持原有紧凑布局。

| 检查 | 结果 |
| --- | --- |
| 静态检查与构建 | ESLint、Stylelint、Prettier、TypeScript 和生产构建通过 |
| 单元测试 | 92 项 / 23 文件通过 |
| 浏览器交互 | 全部 86 项通过，其中 24 项覆盖页面与弹窗帮助、表头操作独立性、触屏开关和短视口内滚动 |
| 视觉场景 | 全部 204 项检查和基线更新通过，包含 24 个帮助场景、100 个页面、60 个浮层、18 个设置分组及 2 个触屏场景 |
| 人工图像复核 | 深浅主题的长说明、短窗口滚动范围、表头问号、SSH、认证组、备份选择与用户/Key 列表完成复核 |
| Flask 入口 | `uv run python -m unittest discover -s tests -p test_ui_entry.py -q`，7 项通过 |
| 生产资源与文本 | 31 个 manifest 资源引用、33 个产物、UTF-8 无 BOM、LF 和 `git diff --check` 通过 |

该轮生产 manifest SHA-256：`2b830bf531dbe76498b7a8e3d14bb8033abb4fd1cf8db6c667cf1788597478e7`。生产资源位于 `src/presentation/static/app/`，继续通过 Python 启动。浏览器使用用户指定的 Chrome CDP 实例和隔离 API 数据，检查不写入真实机器配置或上游账号。帮助视觉基线位于 `frontend/tests/visual/help.spec.ts-snapshots/`。

架构未变化。变更范围为帮助文案、共享帮助展示、对应浏览器验证及生产前端资源。

## Claude Attribution 配置

核对日期：2026-11-07。Claude 配置表单使用共享选择框与悬停帮助管理顶层 `attribution`，默认“不设置”。“禁用”写入 `false`，适用于 Claude Code `2.1.281+`；已有对象显示“文件自定义”，其注释、部分字段及空对象在编辑其他项时完整保留。“不设置”显式移除当前文件的该字段，Attribution Header 和历史 `includeCoAuthoredBy` 独立保留。含义、版本要求与兼容对象写法见 [Claude 配置兼容性](claude-config-compatibility.md#git-与-pr-署名)。

| 检查 | 结果 |
| --- | --- |
| Python 配置读写 | `test_agent_config.py`，51 项通过；覆盖默认省略、禁用回显、显式清空、自定义对象及注释保留 |
| Flask 入口 | `test_ui_entry.py`，7 项通过 |
| 前端静态检查与构建 | ESLint、Stylelint、Prettier、TypeScript 和生产构建通过 |
| 前端单元测试 | 93 项 / 23 文件通过 |
| 浏览器交互 | 全部 87 项通过，包含 Attribution 回显、禁用、清空和帮助悬停 |
| 视觉场景 | 全部 208 项检查和基线更新通过；新增 Attribution 的桌面与窄屏、深浅主题帮助场景 |
| 人工图像复核 | 桌面 Claude 表单、深色 Attribution 帮助及 390px 浅色长说明完成复核 |

当前生产 manifest SHA-256：`308cef1ed0cb2ced8ca9f844a5bd52239b8c1237d9c3e98c39e5d9d9e5b5225b`。31 个资源引用与 33 个产物完整，文本为 UTF-8 无 BOM、LF。配置写入验证使用临时目录，浏览器使用独立模拟数据；未运行 Claude 的 Git 提交或 PR 创建会话。

浏览器联合执行 `playwright test --project=chromium --project=visual --update-snapshots=all`，295 项通过，耗时约 6 分钟。视觉结果包含预期新增字段的基线更新。

架构未变化。该项复用现有配置读取、预览、冲突校验和保存链路。

## API Key 请求限制与列表

核对日期：2026-11-07。API Key 列按完整密钥长度保留宽度，明文使用单行显示，显示与隐藏保持行高；窄屏在表格内横向滚动。列表展示总用量，输入和输出明细列省略。创建与编辑弹窗使用共享数值输入和悬停帮助配置最大并发数、请求/分钟、请求/天，三项留空均表示不限制。

三个限制在后端请求入口生效，覆盖 Chat、Responses、Compact、Messages 和图片接口。自然分钟与自然日按服务器本地时间计数，次数保存于 SQLite；每个客户端推理请求准入一次，内部重试共用该次准入。并发名额在普通响应结束、异常、流式结束或断开时释放。API Key 累计 Token 配额独立生效。Auth Group 提供并发、请求次数与冷却限制，Token 用量用于统计；历史 Token 限速字段兼容读取且忽略，规范化保存与导出省略该字段。

| 检查 | 结果 |
| --- | --- |
| Python 相关回归 | API Key 18、Auth Group 28、Codex 兼容 15、I/O 并发 13、代理流程 130、错误处理 28、告警 3、模型映射 83、Flask 入口 7，合计 325 项通过 |
| 安装与静态检查 | `npm ci`、ESLint、Stylelint、Prettier、TypeScript、相关 Python Ruff 检查与格式化通过 |
| 前端单元测试 | 94 项 / 23 文件通过；限制工作进程数的复核运行正常退出 |
| 浏览器交互 | 92 项通过，包含完整 Key 在 1920px、1440px、390px 和触屏下的布局、旧列宽恢复、限速回显、非法值、清空与保存 |
| 视觉场景 | 分批验证全部 208 项，含 API Key 页面与 Key / Auth Group 弹窗 18 项的预期基线检查和更新 |
| 人工图像复核 | 桌面深色限速弹窗、390px 浅色表单和宽屏列表完成复核 |
| 生产产物 | Vite 生产构建、31 个 manifest 资源引用与 33 个产物完整性、UTF-8 无 BOM、LF、`git diff --check` 通过 |

浏览器使用本机 Chrome、隔离浏览器上下文与模拟 API 数据。触屏视觉测试在完整页面高度的真实视口中截图，保留 `pointer: coarse` 的控件尺寸并匹配已有基线；390px 浅色 JSONC 编辑器路径的中文字体渲染差异经图像复核后纳入基线。工具缓存、测试数据库和调试输出位于项目 `tmp/`；正式截图基线与生产资源位于各自交付目录。

当前生产 manifest SHA-256：`3c438458718404db4cf906e70c9bc3ab7c51df9c848e757813ad95d6f6c50c83`。生产资源随仓库交付，部署继续通过 Python 启动。API Key 准入、内存并发状态和 SQLite 周期计数见 [4+1 架构](architecture-4plus1.md#39-control-plane-api-key-management)。

## Codex 认证文件卡片

核对日期：2026-11-07。认证文件页面首次进入使用“可用”筛选，顺序为可用、额度用完、认证失败、已禁用、其他、全部。没有可用账号时显示对应空状态；存在其他账号时提供“查看全部”入口。搜索、全选和批量操作遵循当前筛选范围。

卡片以账号邮箱为主标题，文件名为次级信息，长名称单行省略并保留完整提示。状态与套餐靠左，刷新和删除靠右。额度区集中展示窗口、剩余比例、重置时间和更新时间；单个窗口占满宽度，多个窗口随卡片宽度排列。查询失败或窗口到期的已有快照显示“上次额度”并使用弱化色，已过的重置时间显示“等待刷新”，原始时间保留在提示中。

请求次数、Token 与预估费用使用对齐的统计列，本期与上期保留各自的详细帮助。重置卡区域仅在存在可用卡时显示，使用前提供确认。错误信息先显示简短摘要，“查看原因”展开完整原文。卡片复用共享控件、语义色和帮助浮层，覆盖深浅主题及窄屏。

| 检查 | 结果 |
| --- | --- |
| 静态检查 | ESLint、Stylelint、Prettier、TypeScript 通过 |
| 前端单元测试 | 94 项 / 23 文件通过；默认筛选断言通过 |
| 浏览器交互 | 全部 96 项通过，包含筛选顺序、重新进入、搜索、筛选内全选、空状态、额度宽度、长名称和错误展开 |
| 视觉场景 | 全部 216 个场景分批通过；22 个 OAuth 场景包含预期基线检查与更新，194 个其他场景完成截图比对 |
| 人工图像复核 | 1440px 深色可用与全部账号、390px 浅色可用账号完成复核；全部账号的四张基线同步最终额度标题 |
| Flask 入口 | `test_ui_entry.py`，7 项通过 |
| 生产产物 | Vite 生产构建、31 个 manifest 资源引用与 33 个产物完整性、UTF-8 无 BOM、LF、`git diff --check` 通过 |

浏览器使用本机 Chrome、隔离浏览器上下文与模拟账号数据。工具缓存、浏览器临时配置和调试输出位于项目 `tmp/`，正式截图基线位于 `frontend/tests/visual/`。生产 manifest SHA-256：`4dc66776e4033308089b68024b1e6e2c31e837b09216e7019ad220dc5332a04d`。生产资源位于 `src/presentation/static/app/`，Python 页面入口按请求读取 manifest。

架构未变化。该项范围为认证文件卡片、默认筛选、共享额度展示、浏览器验证及生产前端资源；后端接口与调度逻辑保持现有行为。

## 列表页面顶部与操作区

核对日期：2026-11-07。Provider、Codex OAuth、模型映射、API Key 和用户页共用 `UiPageHeader`、`UiListToolbar`、`UiSearchInput` 与 `UiSelectionBar`。标题与主要操作位于第一层，搜索、筛选、刷新和“更多”位于第二层；Provider 与 Auth Group 分区位于标题旁，窄屏时独立成行。模型映射数量紧邻标题，OAuth 的状态说明集中于标题问号。字体、控件尺寸、语义色与明暗主题沿用公共设计变量。

导入和导出全部位于“更多”菜单，OAuth 的模型目录也位于该菜单。批量工具仅在选中内容时显示，提供数量、取消选择及对应动作；导出所选与导出全部具有明确的作用范围。OAuth 默认“可用”、末项“全部”的顺序保持一致，当前筛选全选与批量操作共享范围。搜索清空后保留输入焦点；菜单发起弹窗时，以菜单入口作为关闭后的返回焦点，认证文件导入保留原生多文件选择能力。

| 检查 | 结果 |
| --- | --- |
| 静态检查与构建 | ESLint、Stylelint、Prettier、TypeScript 和 Vite 生产构建通过 |
| 前端单元测试 | 94 项 / 23 文件通过 |
| 浏览器交互 | 最终完整轮 103 项通过；包含五页在 1440 / 1024 / 390px 下的工具位置、搜索清空、菜单边界与焦点、批量取消、导出范围和菜单弹窗往返 |
| 视觉场景 | 232 项分批通过；118 个受影响场景完成预期检查及基线更新，114 个其他场景完成截图比对 |
| 人工图像复核 | 桌面深色 Provider、OAuth、模型映射与操作菜单，390px 浅色用户页和 OAuth 批量工具完成复核 |
| Flask 入口 | `test_ui_entry.py`，7 项通过 |
| 生产产物与文本 | 31 个 manifest 资源引用、33 个产物、UTF-8 无 BOM、LF 和 `git diff --check` 通过 |

浏览器使用本机 Chrome、隔离上下文与模拟 API 数据。测试缓存和中间文件位于项目 `tmp/`，正式截图基线位于 `frontend/tests/visual/`。生产 manifest SHA-256：`fa0985cff30cfe2f5754a6974f5420b3ed01fbfc6a3aeb01f9313beb22899a23`。资源随仓库交付，Python 页面入口读取最新 manifest，浏览器刷新后加载当前构建。

架构未变化。当前范围为五个列表页的顶部布局、公共操作组件、组件展示页、验证记录及生产前端资源，业务接口与后端数据处理保持现有职责。

## 系统设置平铺

核对日期：2026-11-07。系统设置在同一页展示六个保存分组，标题区使用共享 `UiPageHeader`。基础设置、客户端 IP、OAuth 网络及日志与调试占满内容宽度，API Key 与模型映射在宽屏并排，700px 及以下上下排列。各面板沿用公共背景、边框、间距和明暗主题；每个分组独立保存，其他分组的草稿在局部保存和失败后保持原值。“重新读取”在存在未保存内容时提供丢弃确认。

ESLint、Stylelint、Prettier、TypeScript、94 项单元测试、103 项浏览器交互、7 项 Flask 入口测试和生产构建通过。232 个视觉场景分批通过，其中系统设置的 28 个页面及分组场景完成预期基线更新，204 个其他场景完成截图比对；1440px 深色整页及 390px 浅色整页完成图像复核。浏览器使用隔离模拟数据，临时内容位于项目 `tmp/`。

31 个 manifest 资源引用、33 个产物、UTF-8 无 BOM、LF 和 `git diff --check` 检查通过。生产 manifest SHA-256：`54097957bbd430843161e3845ddba7b44709b2a72e0dd9f1caf4b1678878f023`。正式资源位于 `src/presentation/static/app/`，浏览器刷新后加载当前构建。

架构未变化。4+1 文档的页面状态描述与当前布局一致，保存接口、草稿隔离和后端配置处理保持现有职责。

## 明亮模式与文字层级

核对日期：2026-11-07。浅色主题使用中性浅灰页面、白色内容面板、浅灰输入背景和灰蓝文字。标题、正文、字段标签与次要信息使用独立语义色；标题及重点文字使用 500 字重，字段标签与表头使用 400 字重。导航选中项、标签页、额度数字、弹窗标题、OAuth 账号和 Agent 机器名称共同使用主题字重变量。占位文字在浅色主题使用完整次要文字颜色，按钮和状态信息使用各自的语义色。

ESLint、Stylelint、Prettier、TypeScript、94 项单元测试、103 项浏览器交互、7 项 Flask 入口测试和 Vite 生产构建通过。232 个视觉场景分批完成：116 个浅色场景包含预期基线更新，116 个深色场景与既有基线对比通过。图像复核涵盖桌面系统设置、Provider 与模型映射表格、OAuth 卡片、Agent 表单，以及 390px 系统设置、登录页和帮助浮层。

40 组常用文字与背景配色通过 WCAG 文字对比度检查，最低为 4.54:1；范围包括正文、标题、标签、次要文字、链接、选中与悬停背景、主按钮及状态配色。浏览器使用本机 Chrome、隔离上下文与模拟 API 数据。工具缓存、测试输出及对比度明细位于项目 `tmp/`，正式视觉基线位于 `frontend/tests/visual/`。

31 个 manifest 资源引用、33 个产物、UTF-8 无 BOM、LF 和 `git diff --check` 检查通过。生产 manifest SHA-256：`13391fe2e22090297a2734ecc0c897f329d9d7f75cebfb346e214ed2f6bd47c8`。正式资源位于 `src/presentation/static/app/`，浏览器刷新后加载当前构建。

架构未变化。该项范围为主题配色、共享文字样式、视觉基线、维护文档与生产前端资源。

## 列表操作与筛选导出

核对日期：2026-11-07。Provider、Auth Group、OAuth、模型映射、API Key 和用户页的新增操作位于列表左侧。支持导入和导出的页面使用常驻按钮；文件导入、导出分别使用 `FileInput`、`FileOutput` 图标。搜索、状态筛选、刷新与辅助工具在宽屏位于右侧，窄屏按可用宽度分行。OAuth 模型目录有直接入口，认证文件导入保留原生多文件选择。

“导出当前筛选”处理状态与搜索条件匹配的所有记录：已启用、已禁用和全部各自使用对应范围，分页列表包含所有匹配页。Provider 导出携带所引用的 Auth Group；用户导出通过 `keyword` 使用与列表相同的用户名和 IP 搜索规则；映射和认证文件显式提交当前匹配的 ID 或文件名。所选导出使用独立勾选范围，空结果和搜索等待期间的导出不可用。下载完成保留列表节点、选择、滚动与搜索状态。

| 检查 | 结果 |
| --- | --- |
| 静态检查与构建 | ESLint、Stylelint、Prettier、TypeScript、相关 Python Ruff 检查和格式化、Vite 生产构建通过 |
| 前端单元测试 | 94 项 / 23 文件通过 |
| 用户与入口回归 | 用户排序、权限与导出 13 项，Flask 页面入口 7 项通过 |
| 浏览器交互 | 108 项分批通过；包含 320 / 390 / 1024 / 1440 / 1920px 操作位置、直接导入、焦点返回、状态与搜索范围、跨页导出、空结果及下载不刷新列表 |
| 视觉场景 | 232 项分批通过；118 个受影响场景完成预期基线更新，114 个其他场景完成既有截图比对 |
| 人工图像复核 | 深浅主题 Provider 操作区、Auth Group、OAuth 卡片与批量操作、用户、API Key、1024px 模型映射和 390px 工具栏完成复核 |

320px 检查涵盖 OAuth 卡片内部的受限单列网格，卡片内容与重置卡控件在可用宽度内排列。浏览器采用本机 Chrome、隔离上下文与模拟 API 数据；Python 测试数据库、工具缓存及调试输出位于项目 `tmp/`。正式视觉基线位于 `frontend/tests/visual/`。

31 个 manifest 资源引用、33 个产物、UTF-8 无 BOM、LF 和 `git diff --check` 检查通过。生产 manifest SHA-256：`c35ae37c64c2093a7e71651b3f96f29b3aee2923d8d3cd0fd01cbfbde7a97274`。正式资源位于 `src/presentation/static/app/`，Python 进程启动时加载用户筛选导出代码，浏览器刷新后加载当前前端构建。

架构未变化。用户导出继续沿用 Controller、Service 与 Repository 的既有职责，数据平面保持现有请求链路。

## Provider 表格与宽屏阅读

核对日期：2026-11-07。Provider 名称使用 16px 重点文字，地址、模型、协议与认证使用 14px 正文字体。认证组和模型详情使用统一的蓝色文字按钮；认证组与 API Key 的起始位置对齐。协议显示为 OpenAI Chat、Responses 和 Claude。

地址与模型列按 3:1 分配额外空间，名称、协议、认证和操作等列保持紧凑宽度。2560px 场景中，模拟网关长地址完整显示为单行；可用宽度不足时使用省略，地址悬停和键盘聚焦展示全文。已有列宽偏好、键盘调整与双击恢复默认值继续生效。批量工具紧随选中数量，取消选择位于工具之后；选中行在悬停时保持选中色。

Provider、模型映射、用户和 OAuth 的按钮文案为“导出”。悬停或聚焦提示导出范围和数量，状态、搜索、所有匹配分页及所选范围使用既有导出规则。Provider 单元格由 `features/providers/ProviderTable.vue` 组合公共表格、文字按钮与提示组件，页面负责既有请求和操作。

| 检查 | 结果 |
| --- | --- |
| 静态检查与构建 | ESLint、Stylelint、Prettier、TypeScript 和 Vite 生产构建通过 |
| 前端单元测试 | 94 项 / 23 文件通过 |
| 浏览器交互 | 111 项通过；包含长地址、认证列对齐、相邻批量操作、选中悬停、列宽偏好、焦点恢复与筛选导出 |
| 视觉场景 | 240 项分批通过；112 项包含受影响基线更新与新 Provider 场景，128 项完成既有截图比对 |
| Flask 页面入口 | 7 项通过 |
| 人工图像复核 | 2560 / 1920 / 1440px Provider、390px 窄屏、深浅主题、混合认证和所选状态完成复核 |

浏览器采用本机 Chrome、隔离上下文和模拟 API 数据。Provider 专用 fixture 包含多种协议、API Key、认证组、无认证、长网关地址、长模型 ID 和禁用记录。工具缓存、测试数据库和调试输出位于项目 `tmp/`，正式视觉基线位于 `frontend/tests/visual/`。

31 个 manifest 资源引用、33 个产物、UTF-8 无 BOM、LF 和 `git diff --check` 检查通过。生产 manifest SHA-256：`aa05d1ffc03b1831c981c1d8aaa3233ea7e559d86d7bc97dd2263cdb48d3de65`。正式资源位于 `src/presentation/static/app/`，浏览器刷新后加载当前构建。

架构未变化。该项范围为展示组件、公共表格宽度分配、批量工具布局、视觉基线、维护文档与生产前端资源。

## Provider 模型详情与删除确认

核对日期：2026-11-07。Provider 单条删除确认显示“是否删除？”。模型列上下预览两条模型 ID，每条使用整列宽度，数量入口在下方左对齐。点击数量入口打开模型详情，支持搜索、复制完整模型 ID、关闭后恢复焦点和再次打开。

Provider API 在没有隐藏模型时省略 `hidden_model_list`。前端类型将该字段声明为可选，模型详情和编辑器按空隐藏列表处理。浏览器通用 fixture 包含省略该字段的响应，Provider 专用 fixture 同时覆盖显式隐藏模型。

ESLint、Stylelint、Prettier、TypeScript、94 项单元测试、113 项浏览器交互、50 个相关视觉场景和 7 项 Flask 页面入口测试通过。视觉范围包括 Provider 页面、工具栏、编辑浮层、模型详情和删除确认，覆盖深浅主题、宽屏与窄屏。鼠标详情回归验证缺省字段、搜索、复制、焦点恢复及重复打开；布局回归验证模型名称的完整行宽与数量入口的位置。

31 个 manifest 资源引用、33 个产物、UTF-8 无 BOM、LF 和 `git diff --check` 检查通过。生产 manifest SHA-256：`1e86532989546f30a8d23298c90e9a39e7daee606bbe857b038b5c4adb97020e`。正式资源位于 `src/presentation/static/app/`，浏览器刷新后加载当前构建。任务临时内容位于项目 `tmp/`。

架构未变化。该项范围为 Provider 展示、可选字段类型与读取、回归用例、视觉基线、维护文档和生产前端资源。

## Provider 列宽、模型标签与确认按钮

核对日期：2026-11-08。Provider 单条删除确认显示“是否删除？”，确认按钮使用实心红底与白色文字。`UiButton` 提供 `danger-solid` 样式，`UiConfirmPopover` 使用相同的确认按钮样式；颜色和悬停状态来自公共语义变量。

Provider 桌面顺序列宽为 96px，名称列默认 176px。名称与地址单行展示，悬停或键盘聚焦可查看全文。地址与模型按 2:3 分配额外空间，地址列上限为 640px，其余可用宽度分配给模型列。名称、地址、协议、认证、排序、勾选、开关和操作按首行对齐。触屏操作目标继续使用 44px 尺寸。

模型列最多显示六个带背景与圆角的标签，并随列宽自动换行。长模型 ID 在标签内省略，悬停展示全文；数量入口在标签下方，打开完整模型详情。`UiBadge size="md"` 提供 14px 标签样式，`UiTable` 提供列宽上限、紧凑排序列与顶部对齐选项。已有列宽偏好、键盘调整和双击恢复默认值继续生效。

ESLint、Stylelint、Prettier、TypeScript、94 项单元测试、114 项浏览器交互和 7 项 Flask 页面入口测试通过。248 个视觉场景分批通过，其中 54 项包含本次展示的基线更新，194 项完成既有截图比对。浏览器检查覆盖六个模型预览、长模型越界边界、单行名称与地址、各列首行对齐、1440px 操作可见性、2560 / 3840px 地址列上限、模型空间分配，以及详情搜索、复制与焦点返回。深浅主题、宽屏与窄屏的 Provider、详情、删除确认、工具栏和公共组件截图完成复核。

Vite 生产构建、31 个 manifest 资源引用、33 个产物、UTF-8 无 BOM、LF 和 `git diff --check` 检查通过。生产 manifest SHA-256：`d011ac0c0ee0e30211bd742c8e520500f2588366278ce0e1bf478211be9dbf74`。正式资源位于 `src/presentation/static/app/`，浏览器刷新后加载当前构建。浏览器使用模拟 API 和隔离上下文，任务临时内容位于项目 `tmp/`。

架构未变化。该项范围为 Provider 列表布局、公共组件的展示选项、交互和视觉回归、维护文档与生产前端资源。

## Provider 地址换行与居中布局

核对日期：2026-11-08。Provider 桌面顺序列宽为 72px，上下移动按钮使用 24px 紧凑尺寸；触屏保留 44px 点击范围。API 地址按可用列宽换行并完整展示，名称、地址、模型组、认证与操作在各自单元格内垂直居中。模型标签使用灰蓝色填充与柔和文字，选中行使用独立标签底色，明暗主题均保持可辨识的标签层次。

`npm ci`、ESLint、Stylelint、Prettier、TypeScript、94 项单元测试、22 项相关浏览器交互、54 个相关视觉场景和 7 项 Flask 页面入口测试通过。浏览器覆盖长地址换行、单元格居中、模型标签边界、列宽偏好、启停稳定性和触屏排序按钮尺寸。视觉范围包含深浅主题、2560 / 1920 / 1440px 与窄屏、选中行、模型详情、删除确认、工具栏及公共组件。

Vite 生产构建、31 个 manifest 资源引用、33 个产物、UTF-8 无 BOM、LF 和 `git diff --check` 检查通过。生产 manifest SHA-256：`501a2336e47a15510e8d2760f1e99f5de4ff41f3b9736e05b37ad6fa7960655e`。正式资源位于 `src/presentation/static/app/`，任务临时内容位于项目 `tmp/`。架构未变化。

## Provider 筛选与名称编辑入口

核对日期：2026-11-08。Provider 操作区按新增、状态筛选、导入、导出排列，状态筛选位于导入左侧。Provider 名称支持点击和键盘打开既有编辑弹窗，关闭后焦点返回名称；名称入口与操作列编辑按钮共用忙碌状态。列表表头显示“上游协议”。桌面排序列为 56px，拖动手柄旁的上下箭头竖向排列，排序表头视觉留空并保留可访问名称。触屏排序列为 96px，按钮保留 44px 点击范围。

ESLint、Stylelint、Prettier、TypeScript、94 项单元测试、34 项相关浏览器交互、38 个相关视觉场景和 7 项 Flask 页面入口测试通过。浏览器覆盖名称编辑与焦点返回、工具栏位置、320–1920px 布局、筛选导出、表格宽度和触屏操作；视觉覆盖 Provider 深浅主题、宽屏与窄屏、工具栏、模型详情及编辑和删除浮层。

Vite 生产构建、31 个 manifest 资源引用、33 个产物、UTF-8 无 BOM、LF 和 `git diff --check` 检查通过。生产 manifest SHA-256：`4b910368dae3b56456bf562fecd783f161da36437de7caef0aa77ff2e2302c09`。正式资源位于 `src/presentation/static/app/`，任务临时内容位于项目 `tmp/`。架构未变化。

## Provider 所选状态与批量工具

核对日期：2026-11-08。Provider 的批量启停入口由所选记录的已确认状态决定：全启用仅显示“禁用”，全禁用仅显示“启用”，混合选择同时显示两项。批量启停只提交需要改变状态的记录；成功后清空选择，失败保留选择和重试入口。单项开关等待期间，批量按钮保持原有显示并禁用，响应成功后同步可用操作。

Provider、用户和 OAuth 共用柔和底色的 `UiSelectionBar`，批量操作采用标准尺寸的次级按钮。删除入口使用 `danger-outline`，尺寸、边框和间距与其他操作一致，红色文字表示删除；最终确认继续使用实心红色按钮。操作紧随选中数量，取消选择使用次要文字色。内容宽度不超过 560px 时，数量与取消选择位于首行，操作在下一行自动换行。

ESLint、Stylelint、Prettier、TypeScript、94 项单元测试、38 项相关浏览器交互、40 个相关视觉场景和 7 项 Flask 页面入口测试通过。交互覆盖三种所选状态、实际批量请求范围、失败重试、成功后状态同步、单项开关等待、取消选择焦点返回与既有导入导出。深浅主题、桌面与窄屏的 Provider 批量工具及 OAuth 共用工具完成图像复核。浏览器采用本机 Chrome、隔离上下文和模拟 API 数据。

Vite 生产构建、31 个 manifest 资源引用、33 个产物、UTF-8 无 BOM、LF 和 `git diff --check` 检查通过。生产 manifest SHA-256：`8f3f94086642bcb851b28304e86347530cb0cf8d169bbf33609ad85bd7095ea4`。正式资源位于 `src/presentation/static/app/`，任务临时内容位于项目 `tmp/`。架构未变化。

## 全局拖动排序与批量操作一致性

核对日期：2026-11-08。Provider、模型映射、Provider 模型编辑表格和映射目标列表共用 `UiDragHandle` 与 `useRowDrag`，界面仅显示拖动把手。顺序列表头视觉留空，宽度统一为桌面 48px、触屏 60px；把手点击范围分别为 32px 和 44px。插入线按落点显示在目标项上方或下方，行间空隙及末尾下方 32px 支持落下，末尾区域仅在拖动期间存在且不占布局空间。

鼠标拖动、触屏移动与取消手势、键盘上下移动和 Home / End 首尾定位共用同一排序协议。触屏靠近滚动区域边缘时自动滚动。外部拖入、原位置落下与操作禁用期间不提交排序，键盘操作保留把手焦点；服务端保存失败保留原顺序与选择并提供重试。Provider 和模型映射在启用、禁用分组内排序，全部视图的跨组落点定位到本组边界，接口仍提交完整顺序。

Provider、模型映射、用户、OAuth 与 Provider 模型编辑的批量操作使用相同的选择栏、按钮尺寸及删除入口样式。OAuth 启停入口遵循与 Provider 相同的全启用、全禁用及混合选择规则，只请求需要改变状态的账号。模型测试、移除与取消选择在模型编辑的共用批量栏内完成。`frontend/AGENTS.md` 与开发文档包含这些组件使用约束。

ESLint、Stylelint、Prettier、TypeScript、94 项单元测试、44 项相关浏览器交互、166 个视觉场景和 7 项 Flask 页面入口测试通过。浏览器覆盖真实鼠标拖至末尾、排序后的保存内容、失败重试、分组边界、键盘焦点、触屏取消与单次提交，以及 OAuth 所选状态规则。视觉覆盖所有主页面的深浅主题和 390 / 1024 / 1440 / 1920 / 2560px，另含排序插入线、各业务的批量工具、Provider / 模型映射弹窗及触屏组件。截图完成重点人工复核；浏览器使用本机 Chrome、隔离上下文与模拟 API 数据。

Vite 生产构建、31 个 manifest 资源引用、33 个产物、UTF-8 无 BOM、LF 和 `git diff --check` 检查通过。生产 manifest SHA-256：`ffd24c435c48fc1b90d79f4a46d51ffea95d79a3554d224036601bcd792013a7`。正式资源位于 `src/presentation/static/app/`，任务临时内容位于项目 `tmp/`。架构未变化。

## Provider 名称链接样式

核对日期：2026-11-08。Provider 名称使用公共 `UiButton` 的链接色，深浅主题分别采用对应的 `--link` 语义色。名称与认证组、模型数量入口遵循同一链接样式，点击编辑、键盘打开和关闭后焦点返回保持可用。

ESLint、Stylelint、Prettier、TypeScript、94 项单元测试、7 项 Provider 浏览器交互、54 个相关视觉场景和 7 项 Flask 页面入口测试通过。深浅主题名称颜色完成图像复核。生产构建、31 个 manifest 资源引用、33 个产物及 UTF-8 无 BOM、LF 检查通过。生产 manifest SHA-256：`90966c4c16886aaaa8f5c05527f612b5ef459f42ad3282aceafc95d718bb053d`。架构未变化。

## Provider 三列表单与地址徽标

核对日期：2026-11-08。Provider 编辑表单在桌面使用三列，900px 及以下使用两列，600px 及以下使用单列。连接方式和自定义代理地址位于同一个网格单元，请求超时和最大尝试次数保持各自的列位置。证书校验、强制上游流式和安全脱敏采用公共 `UiField inline`，标签、悬停帮助与开关在同一行，支持标签点击和键盘切换。

API 地址下方显示已启用的三项功能和已配置的 Hook；关闭功能或清空 Hook 后，相应徽标随保存结果消失。Hook 徽标包含路径，长路径在列内省略，悬停和键盘聚焦显示完整内容。地址与徽标在单元格内作为整体垂直居中，名称继续使用公共链接色。

ESLint、Stylelint、Prettier、TypeScript、94 项单元测试、46 项相关浏览器交互、60 个相关视觉场景和 7 项 Flask 页面入口测试通过。浏览器覆盖自定义代理展开与草稿保留、地址校验、开关标签与键盘操作、保存后的徽标同步、Hook 长路径、明暗主题和窄屏溢出。三列表单、自定义代理、窄屏开关及宽窄表格截图完成重点人工复核；验证使用本机 Chrome、隔离上下文和模拟 API。

Vite 生产构建、31 个 manifest 资源引用、33 个产物、UTF-8 无 BOM、LF 和 `git diff --check` 检查通过。生产 manifest SHA-256：`78e23cd078cabcb7fbd2e18267fd611b17626239f569044f06c9980a08e3b323`。正式资源位于 `src/presentation/static/app/`，任务临时内容位于项目 `tmp/`。架构未变化。

## 表格把手与选择框间距

核对日期：2026-11-08。公共 `UiTable` 的顺序列在桌面为 40px、触屏为 52px，相邻勾选列分别为 32px 和 44px。把手与选择框中心间距分别为 32px 和 44px，把手左侧留白为 8px。Provider、模型映射和 Provider 模型编辑表格使用同一布局；桌面把手点击区域为 32px，触屏把手和选择框点击区域均为 44px。

ESLint、Stylelint、Prettier、TypeScript、7 项相关单元测试、17 项浏览器交互、76 个相关视觉场景和 7 项 Flask 页面入口测试通过。浏览器覆盖勾选、鼠标拖至末尾、键盘移动、触屏拖动及取消，深浅主题的 Provider 和模型映射截图完成间距复核。生产构建、31 个 manifest 资源引用、33 个产物、UTF-8 无 BOM、LF 和 `git diff --check` 检查通过。生产 manifest SHA-256：`3a8557bd9da979a3e9bb0b4ce331a5bf8705e9ace9460f6fa92a193dbe6a0143`。架构未变化。

## Provider 地址列宽

核对日期：2026-11-08。API 地址列与模型列按 5:6 分配额外空间，地址列最大宽度为 720px。宽屏为长地址提供更多空间，超长地址继续在列内换行；模型列最多显示六个标签。已有列宽偏好、键盘调整和双击恢复默认值继续生效。

ESLint、Stylelint、Prettier、TypeScript、5 项表格单元测试、8 项 Provider 浏览器交互、60 个相关视觉场景和 7 项 Flask 页面入口测试通过。宽屏长地址与模型标签完成明暗主题图像复核。生产构建、31 个 manifest 资源引用、33 个产物、UTF-8 无 BOM、LF 和 `git diff --check` 检查通过。生产 manifest SHA-256：`38b647f7a351204bc2589d6c453f3a97603798b7e1a706c251eac512e51acedf`。架构未变化。

## Provider 字段帮助文案

核对日期：2026-11-08。Provider 名称帮助说明模型 ID 前缀及示例，API 地址帮助说明完整上游端点、协议对应路径及示例。上游格式帮助按三种协议展示无序列表，每项按协议名称、常见路径和请求字段分行排列，路径与字段使用等宽字体。静态检查、TypeScript、Provider 弹窗悬停帮助浏览器检查、生产构建及资源校验通过。架构未变化。

## 问号帮助列表与文案

核对日期：2026-11-08。Provider 编辑、模型管理、Auth Group 配置与运行状态、代理连接及安全脱敏的多项说明使用无序列表，每项说明一个规则，保留默认值、计数口径、作用范围与限制。公共 `UiHelpPopover.text`、`UiField.help` 和 `TableColumn.help` 支持字符串与数组：单条说明显示段落，多条说明显示列表；富文本与动态内容使用插槽。浮层统一提供列表缩进、项目间距、自动换行及高度约束，支持悬停、聚焦、触屏与 Escape 关闭。

ESLint、Stylelint、Prettier、TypeScript、94 项单元测试、38 项相关浏览器交互和 44 个相关视觉场景通过。视觉基线对应列表排版，Provider 认证方式、协议、超时、重试、Hook、代理与脱敏帮助完成明暗主题和窄屏图像复核。浏览器使用本机 Chrome、隔离上下文和模拟 API 数据。

Vite 生产构建、31 个 manifest 资源引用、33 个产物与 UTF-8 无 BOM、LF 校验通过。生产 manifest SHA-256：`e088b16e50122bbca1223f5585587893b1dce8c336bfee047ae91315990bfaab`。正式资源位于 `src/presentation/static/app/`。架构未变化。

## 列表状态筛选位置

核对日期：2026-11-08。OAuth、模型映射与组件展示页的状态筛选位于新增操作之后、导入导出之前，与 Provider 页使用相同的 `UiListToolbar.actions` 排列。搜索与刷新等辅助工具靠右。OAuth 在内容区域不超过 1360px 时将左侧操作区独立成行；窄屏的状态选项支持横向滚动，导入导出与搜索按顺序换行。

ESLint、Stylelint、Prettier、TypeScript、13 项列表工具栏交互和 40 个相关视觉场景通过；OAuth 响应式排列的 14 个场景完成最终复核。检查覆盖 320px 至 2560px、明暗主题、筛选与导出范围、批量选择及清空搜索后的焦点。生产构建、31 个 manifest 资源引用、33 个产物与 UTF-8 无 BOM、LF 校验通过。生产 manifest SHA-256：`544ca2b6cc6f2fdf302466699cde28635181c0c5d42eed8f319947ccc35f675c`。架构未变化。

## 编辑弹窗未保存保护与焦点返回

核对日期：2026-11-08。Provider、Auth Group、模型映射、API Key、用户与批量权限、SSH 机器、Agent 路径、OAuth 回调、JSON 导入和模型选择使用公共关闭保护。存在草稿变化时，点击外侧、关闭、取消或按 Escape 显示“继续编辑”和“放弃修改并关闭”。恢复原值可直接关闭；尚未失焦的数值、无效数值、空模型行与未完成的认证 Header 均参与保护。配置文件编辑器继续提供保存、放弃和继续编辑三项选择。

嵌套确认只关闭当前编辑弹窗，焦点在弹窗卸载后返回仍存在的入口；取消确认保留编辑内容。鼠标关闭 Provider 后，名称提示保持关闭；真实悬停和键盘聚焦仍可查看提示。保存期间关闭入口不可用，保存成功直接关闭，保存失败保留草稿与保护。

ESLint、Stylelint、Prettier、TypeScript、94 项单元测试、95 项相关浏览器交互、4 个确认框视觉场景和 7 项 Flask 页面入口测试通过。最终焦点行为另经 10 项相关单元测试与 45 项浏览器交互复核。明暗主题、1440px 和 390px 确认框完成图像检查，浏览器使用本机 Chrome、隔离上下文与模拟 API。

生产构建、32 个 manifest 资源引用、34 个产物、UTF-8 无 BOM、LF 和 `git diff --check` 校验通过。生产 manifest SHA-256：`ca7de0a93e163fa426a396f8fe3cf79c66ec01d2bc0b9296ee9d1a16653a2bdd`。架构未变化。

## 关闭确认的保存入口与值比较

核对日期：2026-11-08。编辑关闭确认使用“继续编辑”“不保存”“保存并关闭”。保存入口复用原表单的校验与持久化；校验失败或保存失败保留草稿并显示错误，成功后关闭。导入、模型选择与 OAuth 使用对应动作名称，SSH 的保存关闭仅保存机器配置。配置文件编辑器提供相同的简短按钮文本。

关闭前提交当前输入框的最新值，并与本次打开时的草稿比较。未修改、修改后恢复原值及等价数字格式直接关闭，无效数值和实际变更继续提示。静态检查、TypeScript、13 项相关单元测试、57 项浏览器交互和 4 个明暗主题、宽窄屏视觉场景通过。生产构建、32 个资源引用、34 个产物和 UTF-8 无 BOM、LF 校验通过；manifest SHA-256：`d5386484e31eef87b65a1de11e8e45beb65671361ef9352046f384427e091e52`。架构未变化。

## 弹窗初始焦点

核对日期：2026-11-08。弹窗初次打开优先聚焦明确指定的输入框或安全操作，没有指定目标时聚焦容器。未保存确认的初始焦点位于“继续编辑”，关闭按钮保持常态，键盘导航仍显示焦点提示。3 项相关单元测试、20 项浏览器交互、4 个明暗主题与宽窄屏视觉场景、TypeScript、格式及产物校验通过。生产 manifest SHA-256：`7d871b55d16833c707c0656667ed8550b3b5bf2f7fa262645b04347de1e936fa`。架构未变化。

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
## Provider 模型表格行内操作

核对日期：2026-11-08。模型表格底部提供整行“添加”，空表可直接添加，新增后聚焦模型 ID 输入框。每行眼睛图标切换模型目录可见性，表头眼睛在全部可见时执行全部隐藏，其余状态执行全部显示；问号说明和列宽操作独立。新增行入口不参与排序、勾选或统计，窄屏时入口文字保持在可视宽度内。修改随 Provider 草稿保存，空行继续参与未保存保护。

TypeScript、ESLint、Stylelint、Prettier、94 项单元测试、31 项相关浏览器测试和 18 个明暗主题视觉场景通过，完成桌面深色与平板浅色截图检查。390px 下另外复核添加入口可视范围、自动聚焦、逐项与整列切换及保存内容。正式构建包含 32 个 manifest 资源引用、34 个产物，UTF-8 无 BOM、LF 和 git diff --check 校验通过。生产 manifest SHA-256：`f253b0f8ccc5fa9e13fdd6ed087eea6d36d80928084145f06642437987d6f778`。架构未变化。
## 模型测试分列与计量审阅

核对日期：2026-11-08。Provider 模型表格采用“结果、首字延迟、TPS”三列，结果后无问号，指标口径集中在对应表头。显示列说明包含目录展示与隐藏行为。错误文本位于操作列，长内容省略并支持悬停与键盘聚焦查看全文；批量按钮为“测试”“删除”。后端计时、吞吐量公式与测试参数详见 [Provider 模型测试口径](provider-model-testing.md)，本次保留其实现。

TypeScript、ESLint、Stylelint、Prettier、7 项相关前端单元测试、13 项浏览器交互、20 个视觉场景及 17 项后端模型测试通过。初次新增浏览器用例的表头精确名称断言未包含列宽手柄的可访问名称，调整定位后通过。已检查深浅主题成功与失败结果截图；测试采用模拟 API 和模拟上游，无真实上游调用。正式构建、32 个资源引用、34 个产物及 git diff --check 校验通过。生产 manifest SHA-256：`bb270ab34176de67c9b0576be0bcd33344b2aed10ca6330244e938add3221b08`。架构未变化。
## 模型错误信息完整展示

核对日期：2026-11-08。Provider 模型测试的错误信息直接完整显示在操作列，保留原文换行，长单词可在列内折行，行高随内容扩展。错误文本不省略，也没有额外悬停浮层。浏览器回归检查错误内容完整、实际折行以及宽高均无裁切；深浅主题截图完成复核。

TypeScript、相关 ESLint、Stylelint、Prettier、1 项浏览器交互和 2 个视觉场景通过。生产构建及资源校验通过，manifest SHA-256：`07634cf12155c900d5a64c903bf97ffac47d161fad0f69916b85d28e5d8c2278`。架构未变化。

## 模型表格列宽微调

核对日期：2026-11-08。模型列基础宽度为 320px，操作列为 155px，表格基础总宽度保持一致，错误信息在操作列内完整换行。20 个相关视觉场景、格式与构建资源校验通过，已复核桌面深色错误展示。生产 manifest SHA-256：82645ffeabb378906bfadf5b0595b3d5941846c99166a70f5743e3e3f82763ff。架构未变化。
## TTFT 与生成阶段 TPS

核对日期：2026-11-08。首字延迟从发起上游请求计至首个非空正文或思考片段，排除本地请求准备和此前失败尝试。TPS 为 `(completion_tokens - 1) / (末个输出片段时间 - 首个输出片段时间)`，单位 Token/s。数据块到达时间参与计算，空角色、心跳、用量和终止事件不延长输出区间。单数据块、缺少用量、输出不超过一个 Token、无有效区间、已知隐藏思考或摘要样本显示 —。

Provider 表头和模型映射测试详情复用同一帮助文案，说明公式、单位、思考范围和分块误差。26 项模型测试、30 项控制器及用量回归、13 项前端单元测试、1 项浏览器交互与 8 个帮助视觉场景通过；已检查窄屏深色 TPS 与桌面浅色 TTFT 浮层。ESLint、Stylelint、Prettier、TypeScript、Ruff、正式构建及资源校验通过。测试使用模拟上游和 API，没有实际付费模型请求。生产 manifest SHA-256：`51e7e481d4be748d3689c412a72c0e04610d0232aee6abced2d601180f50887e`。架构未变化。

## 模型表格操作列对齐

核对日期：2026-11-08。操作列表头、按钮组和完整错误文本水平居中。20 个相关视觉场景通过，已复核深色成功与失败结果；格式、样式及生产资源校验通过。生产 manifest SHA-256：3566a75d40f875320c350f29ada9169f250849f9bd9ad01e4e6ff8cbebcae198。架构未变化。

## 自定义代理紧凑输入与凭据校验

核对日期：2026-11-08。自定义代理输入位于连接方式下方，说明问号位于输入框右侧，代理地址标签仅供屏幕阅读器使用。Provider 与 OAuth 的前端校验按最后一个 @ 分离凭据并校验代理端点，原始凭据交由服务端自动编码；有效百分号编码保持原样。帮助说明包含用户名冒号、字面百分号编码与 SSH 代理手动编码规则。

TypeScript、ESLint、Stylelint、Prettier、11 项前端单元测试、3 项浏览器交互、10 个明暗主题及响应式视觉场景和 2 项后端凭据编码测试通过，另有 3 个编码样例完成验证。桌面深色布局与窄屏浅色帮助完成图像检查。正式构建、34 个 manifest 资源引用与 36 个产物校验通过。生产 manifest SHA-256：`4be98941055b2aa64c232138b3fadbec802a60c4a97d65f2dfeeed3376e362d9`。架构未变化。

## 自定义代理同行排列

核对日期：2026-11-08。自定义代理模式的选择器宽度为 124px，地址输入和右侧问号与选择器同行排列，输入框占用剩余宽度。切换连接方式保留地址草稿，错误提示保持字段关联。

TypeScript、相关 ESLint、Stylelint、Prettier、3 项浏览器交互与 10 个明暗主题及响应式视觉场景通过，桌面深色与窄屏浅色截图完成复核。正式构建及资源校验通过。生产 manifest SHA-256：`ebed8cbcf8f4f25828aaeaa7cc7fcb148740b6e264f09be7b4ac4a63c27a25ce`。架构未变化。

## 代理认证独立输入

核对日期：2026-11-08。代理地址行包含紧凑选择器、地址、认证按钮与问号。认证开启时，下方同行显示用户名和默认隐藏的密码，密码支持眼睛切换。凭据按原文编码一次，已有 URL 与粘贴 URL 自动拆分；关闭认证后提交地址不包含凭据，重新开启保留本次草稿。旧 URL 未编辑或恢复原值时保持原始写法，避免编码格式差异触发未保存提示。

Provider、OAuth、SSH 复用该组件。SSH 关闭原有代理认证时通过 `proxy.clearCredentials` 显式清除当前会话的代理凭据，原有未指定此字段的请求继续沿用凭据；SSH 密码与登记文件规则保持一致。4+1 文档记录该内存状态操作。

TypeScript、完整 ESLint / Stylelint / Prettier、24 项相关前端单元测试、9 项浏览器交互、10 项视觉测试（16 个截图场景）和 4 项后端凭据测试通过。检查覆盖特殊字符与字面百分号往返、旧 URL 回填、认证关闭与恢复、恢复原值、SSH 保存、系统设置帮助，以及桌面与窄屏的明暗主题。正式构建与 33 个资源引用、35 个产物校验通过。生产 manifest SHA-256：`5fcd34066648fb9f123e64400ab560a7333c713d85a5810b78894f888b6ac51c`。前端资源刷新生效，SSH 显式清除凭据需要重启 Python 服务加载。

## 代理输入帮助文案

核对日期：2026-11-08。代理地址帮助分别说明地址示例、支持协议、认证展开、原文凭据输入、完整地址粘贴和域名解析位置；特殊字符使用「@ 不用写成 %40」示例。4 项明暗主题及桌面、窄屏帮助视觉检查通过，格式、生产构建及资源校验通过。生产 manifest SHA-256：`b07658dfb451b36790a4c8c175f47f56b6d0c0326b0ab3434ebf471fa75e3da6`。架构未变化。

## 自定义代理选择器宽度

核对日期：2026-11-08。自定义代理选择器宽度为 108px，`UiSelect.compact` 提供较小的水平内边距和箭头间距，标签完整显示，地址输入占用剩余空间。TypeScript、相关 ESLint / Stylelint / Prettier、10 项视觉测试（16 个截图场景）、正式构建与资源校验通过。生产 manifest SHA-256：`a0515b60a81d74d9627ba1b2915ad746f633f987c1326f0977aae421dc10ae53`。架构未变化。

## 代理认证按钮默认边框

核对日期：2026-11-08。代理认证按钮关闭时使用公共 secondary 样式显示边框，开启时使用 primary 高亮。格式、10 项相关视觉测试、正式构建及资源校验通过。架构未变化。

## 主动关闭与统一代理认证浮窗

核对日期：2026-11-08。编辑弹窗的取消、右上角关闭及文件编辑器页脚关闭直接放弃草稿；外侧点击与 Escape 保留未保存保护，保存期间不可关闭。frontend/AGENTS.md 与组件开发文档记录该行为。

Provider、OAuth 网络与 SSH 机器共用认证编辑浮窗。代理行始终包含选择器、地址、认证按钮和问号；点击认证在按钮旁填写用户名与密码，已设置时高亮。完成、关闭、Escape 或点击外侧只收起浮窗并保留表单草稿，外层保存提交配置；清除认证移除账号密码。旧 URL 自动拆分、百分号还原及原文编码规则保持一致。公共 UiPopover 提供定位、标题、主题、焦点及嵌套交互，组件展示页包含实例。OAuth 与 SSH 自定义代理使用所在表单整行宽度。

119 项前端单元测试、46 项相关浏览器检查通过；认证浮窗最终行为经 6 项单元测试及 Provider、OAuth、HTTP/HTTPS SSH 保存检查复核。TypeScript、完整 ESLint / Stylelint / Prettier、38 项视觉检查通过，已查看桌面深色 Provider 与窄屏浅色 OAuth 认证浮窗。检查包含父表单高度稳定、浮窗关闭不触发外层确认、旧凭据回填、编码保存和清除认证。正式构建、33 个资源引用与 35 个产物校验通过。生产 manifest SHA-256：`17d61707a947fb99670f407be9269680578b12839d20fa02f0c74ff9e630ec8b`。架构未变化。

## 认证清除文案与浮窗尾巴

核对日期：2026-11-08。代理认证操作使用「清除」。编辑浮窗和全局问号说明共用 UiPopoverArrow，尾巴跟随触发控件及碰撞翻转方向，填色采用浮层主题色；编辑浮窗尾巴包含对应边框。长内容使用内部容器滚动，尾巴位于滚动区域之外。

TypeScript、完整 ESLint / Stylelint / Prettier、6 项相关单元测试、29 项浏览器交互与 56 项视觉测试通过，覆盖各页面问号、移入保留、触屏点按、长内容滚动、旧代理凭据回填及清除。桌面深色认证浮窗与窄屏浅色 TPS 说明完成图像复核。正式构建、33 个资源引用与 35 个产物校验通过。生产 manifest SHA-256：`03a3e70353494cfcd73725273f1e293d6ef5ca57fece2ff74fe4c4c2e811b3e2`。架构未变化。

## 上游模型勾选同步

核对日期：2026-11-08。「选择上游模型」按当前草稿预选已有候选，「确定」同步新增和取消勾选的模型；候选之外的手填模型保留，保留行的顺序、隐藏状态及测试结果保持一致。移除行的测试请求取消，批量选择同步清理。搜索保留其他候选的选择，全部取消勾选仅移除本次候选。外层 Provider 保存提交配置。

TypeScript、完整 ESLint / Stylelint / Prettier、13 项相关单元测试与 20 项浏览器交互通过，覆盖取消勾选、跨搜索选择、全不选、隐藏状态、保存后重新打开及嵌套关闭保护。正式构建、33 个资源引用与 35 个产物校验通过。生产 manifest SHA-256：`d14437e9568618d73d5ed23b15dda4ef7c25a086ba82b4b9dc02413881d06d78`。架构未变化。

## 模型测试按行锁定

核对日期：2026-11-08。模型 ID、测试与删除按当前行的请求状态禁用，其他行可独立测试、编辑与删除，排序使用稳定行 ID 保持请求结果归属。批量测试保持串行，等待行可编辑与移除；批量删除包含正在测试的行时不可用。停止及连接配置变化取消请求和等待队列，旧请求完成不会覆盖新请求结果或解除其锁定。全表整理在测试期间不可用。

TypeScript、完整 ESLint / Stylelint / Prettier、11 项模型单元测试与 14 项浏览器交互通过。检查覆盖独立并发测试、乱序返回、测试中鼠标拖动至末尾、等待行编辑及移除、停止后重测、保存模型顺序。正式构建、33 个资源引用与 35 个产物校验通过。生产 manifest SHA-256：`17df485350be5da85be9adb968d3ab84ec5f40dede488bfd90e3609fe6160876`。架构未变化。

## 确认气泡与悬停提示尾巴

核对日期：2026-11-08。UiConfirmPopover 和 UiTooltip 使用共用 UiPopoverArrow，与问号说明、认证浮窗保持相同的圆润尾巴及主题填色。定位组件按触发按钮与视口空间计算尾巴位置和浮层方向，确认、取消和焦点恢复沿用公共行为。

TypeScript、完整 ESLint / Stylelint / Prettier、24 项浏览器交互和 28 项视觉检查通过，覆盖单项及批量删除、悬停提示、问号和代理浮窗，包含桌面与窄屏、明暗主题。批量删除深色桌面、悬停提示与问号浅色窄屏截图完成图像复核；历史无尾巴的问号截图同步为当前界面。正式构建、33 个资源引用和 35 个产物校验通过。生产 manifest SHA-256：`bac27dbd23a788fa250a77fdac5c966a532a3e24c86d1e0a0a1af19bb30ce9b3`。架构未变化。

## 多选清空入口

核对日期：2026-11-08。UiSelectionBar 在「已选 N 项」后紧邻显示小号 ×，可访问名称与带尾巴的悬停提示均为「清空选择」。数量与清空入口始终保持一组，批量工具紧随其后，窄屏下批量工具另起一行。Provider、OAuth、模型映射、用户、Provider 模型列表及组件展示页共用此交互。清空仅取消勾选，保留原勾选框焦点恢复及忙碌禁用规则。

TypeScript、完整 ESLint / Stylelint / Prettier、21 项浏览器交互和 64 项视觉检查通过，涵盖鼠标、键盘、清空后焦点、无写入、桌面与窄屏的明暗主题；桌面深色 Provider 与窄屏浅色 OAuth 截图完成图像复核。正式构建、33 个资源引用与 35 个产物校验通过。生产 manifest SHA-256：`367dc1ba01158490517af7785baf74125e8de4ccac5765f2acc0d4b25cb50fe9`。架构未变化。

## 拖动排序性能

核对日期：2026-11-08。共用排序模块按动画帧合并移动事件，复用候选行索引并通过二分查找读取实时位置。插入线只更新目标行属性，移动期间业务单元格不重渲染；滚动、窗口变化和列表更新刷新落点。松手按最终坐标提交一次排序，结束时清理帧任务、插入线和监听。原生提示保持现状。

19 项相关单元测试、11 项浏览器检查和 4 项明暗主题及响应式拖动视觉检查通过。128 行单元测试覆盖事件合并、行索引复用、单元格渲染次数不变、滚动刷新和最终落点；64 个 Provider 的浏览器测试连续触发 200 次移动，下一帧行位置读取不超过 10 次，单元格 DOM 变更为 0。实际鼠标、触屏、分组、首尾、取消、保存失败和测试中排序检查通过。TypeScript、完整 ESLint / Stylelint / Prettier、正式构建、33 个资源引用与 35 个产物校验通过。生产 manifest SHA-256：`1551502b825ffb65d5626e1fe3c3fc8aba9bf16f639fa60d0db86abc4a312dd6`。架构未变化。

## 模型测试凭据位置

核对日期：2026-11-08。测试 Auth Entry 位于模型顶部操作区，标签、问号与选择器横向排列在整理、停止测试及拉取模型按钮左侧。窄屏自动换行。凭据选择、默认启用条目及请求参数保持原有行为。

TypeScript、完整 ESLint / Stylelint / Prettier、11 项模型单元测试及 4 项明暗主题和窄屏视觉检查通过，桌面深色与窄屏浅色截图完成复核。正式构建与资源校验通过。生产 manifest SHA-256：`e1dd65c8158497aa0d2bf3cef10220a9d8490ac474af663640f79c92d5e98537`。架构未变化。

## 气泡尾巴接缝

核对日期：2026-11-08。共用尾巴使用独立填色与斜边描线，底部填色延伸进浮层覆盖横向边框及轮廓阴影，尾巴与浮层连续连接。上下定位及明暗主题保持同一绘制规则。

TypeScript、完整 ESLint / Stylelint / Prettier、30 项相关视觉检查通过，覆盖 Provider、OAuth、SSH 认证浮窗、问号、确认气泡和普通提示。桌面深色 Provider、OAuth 与窄屏浅色 SSH 的上下接缝完成图像复核。正式构建与资源校验通过。生产 manifest SHA-256：`a532e7fa38c17f6a8a47869066c10515f3186f9a4d963b0229ee827b58595300`。架构未变化。

## 名称直接编辑入口

核对日期：2026-11-08。Auth Group 名称和模型映射的对外模型 ID 使用公共链接按钮，点击或按 Enter 打开对应编辑弹窗，关闭后恢复入口焦点。名称与操作列编辑按钮共用原有流程及忙碌保护，模型 ID 复制入口独立。

TypeScript、完整 ESLint / Stylelint / Prettier、14 项相关浏览器交互和 8 项明暗主题、桌面及窄屏视觉检查通过；Auth Group 名称的鼠标、键盘及焦点返回，以及模型映射名称进入编辑后保存均完成检查。桌面深色 Auth Group 截图完成图像复核。正式构建与资源校验通过。生产 manifest SHA-256：`cbc794a200da028f56d0a45087c2f82d16c0bdc38cffd1cb24d6a2de093baf29`。架构未变化。

## 编辑器字号与认证组导入导出

核对日期：2026-11-09。公共 Ace 编辑器使用 15px 字号和 6px 行号左侧内边距，行号与正文位置由 Ace 按位数计算，保留折叠按钮。Provider 导入、认证组 YAML、模型映射导入与认证组导入覆盖桌面、窄屏及明暗主题。

Auth Groups 工具栏直接提供导入、导出入口，导出包含当前搜索匹配的所有认证组，空结果时禁用。组级 JSON 文件包含策略、条目限额及完整认证请求头。同名组保留现有设置和同 ID 凭据，只补入缺少的条目；配置通过完整校验后一次写回并重载。接口使用管理认证，认证组业务与 Provider 导入共用合并规则。

78 项相关后端测试、7 项前端单元测试、9 项浏览器交互和 20 项视觉场景检查通过。覆盖认证保护、往返导入导出、筛选范围、空结果、错误内容保留、无效批次不写回、原有凭据与 Provider 绑定保留；桌面深色 YAML 与窄屏浅色导入截图完成图像复核。TypeScript、完整 ESLint / Stylelint / Prettier、Python import 整理和格式化通过。正式构建、33 个资源引用、35 个产物与本轮文本编码校验通过。生产 manifest SHA-256：`5e917ea833307de33c48521faa78334808f49b7f9ffc7a578e21b2d9fe47f2ce`。架构未变化。

## 认证条目侧栏与统一编辑导入

核对日期：2026-11-09。认证条目侧栏每行提供名称、启用开关与移除图标，添加入口位于列表末尾。启停保留当前选择，移除当前条目后选择相邻项；移除全部条目后仍可从末尾添加。右侧表单从 Entry ID 开始，窄屏使用上方条目列表。

“编辑 / 导入”预载当前 YAML，支持文件选择、粘贴与直接编辑，确定后更新当前表单，保存认证组后生效。`TextImportDialog` 与 `JsonImportDialog` 共用文件加载、编辑器、错误和关闭保护。读取文件期间禁止重复操作，关闭窗口后取消解析请求。校验失败保留编辑内容，取消和关闭直接放弃当前窗口草稿。

23 项相关浏览器交互与 20 项视觉检查通过，覆盖侧栏操作、相邻选择、空列表添加、导入失败重试、两级保存、未保存关闭保护、Provider JSON 导入及明暗主题和窄屏显示。桌面深色侧栏、窄屏浅色侧栏与合并编辑窗口完成图像复核。TypeScript、完整 ESLint / Stylelint / Prettier、正式构建、31 个资源引用与 33 个产物校验通过。生产 manifest SHA-256：`5274a0dbcdffd098e84ec40762313a4b4b9bfee8e8e3b02762fc955eb9446027`。架构未变化。

## 认证运行状态直接展示

核对日期：2026-11-09。认证组运行状态使用公共弹窗的上方定位，顶部间距为 24–96px，长内容在弹窗内部滚动。刷新入口为单个图标按钮，刷新与重置保持顶部位置及现有表格节点。请求列按分钟、当天显示用量和配置上限；最近结果列直接展示 HTTP 状态码、错误类型、完整错误信息及更新时间。手动禁用通过状态与开关展示，自动禁用原因与 HTTP 状态码去重；所有信息位于同一运行状态窗口。

4 项对话框单元测试、4 项相关浏览器检查、6 项明暗主题和响应式视觉检查通过。覆盖直接读取结果、请求上限、隐藏手动原因、刷新保留节点、重置更新结果、原开关流程、问号说明，以及短窗口内 24 条记录的滚动与关闭。1440px 深色与 390px 浅色截图完成复核。TypeScript、完整 ESLint / Stylelint / Prettier、正式构建、31 个资源引用、33 个产物与文本编码校验通过。生产 manifest SHA-256：`d46eb9d8e517161c7fec21efa2a8e8b87ac93176d12499340e262ce08286893e`。架构未变化。

## Auth Groups 多选与批量操作

核对日期：2026-11-09。Auth Groups 复用公共表格的单选、全选与半选状态，选择后展示统一的数量、清空、导出所选和删除操作栏。选择与 Provider 独立，搜索或切换标签时清空，刷新时保留仍存在的选项。批量删除使用管理认证和配置写锁，完整验证名称及 Provider 引用后一次写回并重载；校验失败整批保留，前端保留选择以便重试。

50 项相关后端测试、11 项浏览器检查和 8 项明暗主题、桌面及窄屏视觉检查通过。覆盖批量删除单次写回、重复名称去重、禁用 Provider 的引用保护、无效选项整批保留、接口认证、筛选全选、清空焦点、导出范围、删除失败重试、同名 Provider 选择隔离和刷新后选择同步。桌面深色与窄屏浅色多选截图完成复核。TypeScript、完整 ESLint / Stylelint / Prettier、Python import 整理和格式化、正式构建、31 个资源引用及 33 个产物校验通过。生产 manifest SHA-256：`60d133498b8c8fd6bd7f90c4372a2670a14e04725b4fa43766f09f634c56e8d1`。架构未变化。

## 认证条目选中样式

核对日期：2026-11-09。`UiSelectableRow` 提供整行选中底色与名称高亮，名称和操作区域形成连续背景，名称按钮使用透明边框。开关和移除保持独立操作，选中与悬停使用公共语义色，明暗主题及窄屏一致。正式组件位于组件展示页。

2 项现有认证组交互测试、4 项明暗主题及桌面/窄屏视觉检查通过，桌面深色和窄屏浅色截图完成复核。TypeScript、完整 ESLint / Stylelint / Prettier、正式构建、31 个资源引用与 33 个产物校验通过。生产 manifest SHA-256：`7b40298c06b710c950e6e0d8238877fc5e6d37e1d6c7b0b6d668cadcbc74055f`。架构未变化。

## 确认气泡内容层级

核对日期：2026-11-09。公共确认气泡以独立类名提供标题、说明和右对齐操作区。重置确认展示简短标题与两条无序列表说明，长段文字拆分为状态清除和用量清零；正文使用次级文字色，按钮保留明确的取消与红色确认层级。普通删除确认使用相同的字号、间距和操作布局。

6 项相关交互检查与 14 项视觉场景通过，覆盖重置、条目移除、批量删除、Provider 删除确认，以及明暗主题和窄屏。桌面深色、窄屏浅色重置气泡完成图像复核。TypeScript、完整 ESLint / Stylelint / Prettier、正式构建、31 个资源引用和 33 个产物校验通过。生产 manifest SHA-256：`30b891368c565c92ff061149f9b385731e582b302b2c62ebb75908516cb09189`。架构未变化。

## OAuth 筛选与授权链接操作

核对日期：2026-11-09。状态筛选展示可用、额度用完、认证失败、已禁用与全部，其他不可用账号仍包含在全部中。添加账号弹窗在授权链接右侧展示复制链接和打开链接，窄屏操作自动换行右对齐。

14 项表单及 OAuth 交互检查、12 项明暗主题和桌面/窄屏视觉场景通过，桌面深色与窄屏浅色授权弹窗完成截图复核。TypeScript、完整 ESLint / Stylelint / Prettier、正式构建、31 个资源引用和 33 个产物校验通过。生产 manifest SHA-256：`611443c26469e9979060fd3776e7b0226c206173d9aa8c9955a344901e5131b1`。架构未变化。

## 2.4.0 提交验收

核对日期：2026-11-09。项目与前端包版本为 2.4.0，正式静态资源随仓库交付。提交包含统一组件、明暗主题与侧栏布局、Provider 及代理编辑、Auth Group 导入导出与多选、API Key 限速、Agent 配置和模型测试指标。控制平面接口与状态相关架构图同步记录在 `architecture-4plus1.md`。

151 项相关后端测试、124 项前端单元测试通过。154 项浏览器回归中，153 项首次通过；宽屏布局测试的顺序列断言为 40px、API 地址列上限为 720px，两项宽屏场景复验通过。TypeScript、ESLint、Stylelint、Prettier、Python import 与格式检查、生产构建及资源引用校验通过。提交文本使用 UTF-8 无 BOM、LF。本机 `config.yaml` 的 Provider 配置保留在工作区。
