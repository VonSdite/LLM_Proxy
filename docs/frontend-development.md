# 前端开发与验收

管理界面使用 Vue 3 Composition API、TypeScript strict、Vite 和 Vue Router。Flask 为明确的页面路径提供同一个入口，`/api/ui/bootstrap` 提供会话、版本、功能开关和访问能力。生产版本使用 `src/presentation/static/app/`，Node.js 用于开发、测试和构建。

## 开发环境

Node.js 24.13.0 及同系列兼容版本、npm 11、Python 3.12+、uv。前端依赖由 `frontend/package-lock.json` 固定。

```text
uv sync
uv run python main.py --config config.yaml
```

另一个终端运行：

```text
npm --prefix frontend ci
npm --prefix frontend run dev
```

开发地址为 `http://127.0.0.1:5173`。Vite 的 `/api`、`/static/vendor` 和 `/logout` 代理默认指向 `http://127.0.0.1:8080`。后端端口不同时，在 PowerShell 7 中设置：

```powershell
$env:LLM_PROXY_DEV_URL = "http://127.0.0.1:9090"
npm --prefix frontend run dev
```

管理 API 代理保持浏览器 Host / Origin，Agent 接口仍校验同源请求、浏览器会话与 `X-Agent-Config: 1`。开发期间使用独立配置和数据目录。

## 组件与状态

| 需求 | 入口 |
| --- | --- |
| 操作、字段标签和错误 | UiButton、UiIconButton、UiField |
| 文本、数值、密码 | UiInput、UiNumberInput、UiSecretInput |
| 固定选择、搜索/手填、多选筛选 | UiSelect、UiCombobox、UiMultiSelect |
| 开关、复选、三态 | UiSwitch、UiCheckbox、UiTriState |
| 分页、表格、拖动列表 | UiPagination、UiTable、UiSortableList |
| 简短提示、帮助 | UiTooltip、UiHelpPopover |
| 对话框、操作确认 | UiDialog、UiConfirmPopover |
| 状态、消息、空数据 | UiBadge、UiStatus、UiAlert、UiEmptyState、notify |
| 文件编辑 | UiCodeEditor；业务使用 ConfigFileEditor |

基础组件通过 `@/components/ui` 导入。业务页面的 CSS 负责布局，颜色、边框、阴影、焦点和层级来自 `styles/tokens.css`。控件视觉和交互在组件内统一维护。`/dev/components` 使用正式组件，生产构建不包含该页面。

`UiSelect` 与 `UiCombobox` 的 `option` 插槽接收 `{ option }`，可展示模型名、数值和来源；选中控件显示 `label`，保存值使用 `value`。桌面控件为 32/36px，`pointer: coarse` 下常用控件点击区域至少为 44px，复选框与开关的图形仍保持紧凑。

Reka 的 Portal、Presence 等包装层与实际 DOM 的 scope 属性可能不同。浮层和通知最外层使用唯一的 `ui-*` 类及 `:global()` 选择器，内部布局继续采用 scoped 样式。浏览器检查包括实际背景、定位与层级。需要保留字段或说明 ID 时，使用 `as-child` 并将 ID 放到原生元素；标签和 `aria-describedby` 始终关联实际控件。

页面组合 `features` 的业务组件，业务通过 `api` 访问服务。Pinia 保存会话、功能开关和主题；表单草稿、机器、分页和测试结果属于对应页面。主题兼容 `localStorage.theme`，默认深色。表格列宽按 `storageKey` 存入 `table-widths:*`，凭据不写入本地存储。

模型权限使用 ModelPermissionPicker，API Key 使用 ApiKeyField，代理设置使用 ProxySettings，路径使用 ConfigPathRow，文件使用 ConfigFileEditor。Agent 配置解释与清空规则集中在 `features/agents/configuration.ts`。

`UiField` 的普通模式关联标签、错误和控件；复合字段使用 `group`，内部输入分别标注名称。数值输入的 `update:modelValue` / `commit` 在失焦或 Enter 且数值变化时触发，候选选择立即提交；输入期间保留原草稿。业务使用 `validity-change` 或表单原生校验阻止非法值提交。

列表以持久业务 ID 为 key，新行使用独立临时 ID。`useRequest` 取消被替代的读取并丢弃迟到结果；`useAction` 串行执行操作、集中显示错误，回调接收取消信号；自定义流程使用 `useLifetime` 并在异步步骤后检查信号。服务端已经完成的写入仍然生效，取消请求不提供事务回滚。

帮助支持悬停、聚焦和移入内容；对话框负责焦点约束、Escape、遮罩与关闭后恢复。文件编辑器通过 `beforeClose` 显示保存、放弃和继续编辑。Ace 从本地 vendor 按需加载，组件卸载时销毁实例。

## 验证

```text
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test:unit
npm --prefix frontend run build
uv run python -m unittest discover -s tests
uv run python scripts/check_agent_model_limits.py
```

浏览器测试使用独立 API fixture，拦截全部 `/api/*` 请求，不写入真实用户配置、SSH 机器或数据库。未声明请求作为失败证据记录。`--list` 仅检查用例发现，不执行浏览器。

在可用的开发或 CI 浏览器环境运行：

```text
npm --prefix frontend run test:e2e
npm --prefix frontend run test:visual
```

Playwright 使用 Chromium、中文环境、上海时区和减少动画设置。测试服务器以 test 模式启动，允许打开组件展示页。失败截图和 trace 位于 `frontend/test-results/`，HTML 报告位于 `frontend/playwright-report/`。

已有获准的 Chrome CDP 实例时，可在 PowerShell 7 中指定地址；每个用例仍创建独立浏览器上下文：

```powershell
$env:PLAYWRIGHT_CDP_ENDPOINT = "http://127.0.0.1:9222"
npm --prefix frontend run test:e2e
npm --prefix frontend run test:visual
```

没有指定 CDP 时使用 Playwright 管理的 Chromium；开发机与 CI 可运行 `npx --prefix frontend playwright install chromium` 安装对应浏览器。Windows 截图基线使用 Chrome 144.0.7559.60。截图 fixture 在页面加载前统一隐藏系统滚动条；交互测试保留原生滚动条并检查滚动、点击和溢出。日期相关截图使用 `page.clock.install({ time })`，时间从指定值继续推进，Vue 事件监听器的时间戳筛选保持有效。测试报告和截图目录不参与 Vite 文件监听。

首次建立截图基线：

```text
npm --prefix frontend run test:visual -- --update-snapshots
```

逐张复核后提交基线。使用相同操作系统、字体和浏览器版本比较，检查明暗主题、对比度、长模型/路径、空数据、错误、加载、禁用、窄屏和关键浮层。Agent 输入时的光标、滚动、预览稳定性和密码填充需要实际浏览器检查。

证据和验证边界统一记录在 [迁移验收记录](frontend-migration-audit.md)，包含旧用例对应关系。组件和 jsdom 测试覆盖状态逻辑，浏览器测试覆盖实际交互，截图和人工检查覆盖视觉质量。

## 构建与部署

```text
npm --prefix frontend ci
npm --prefix frontend run build
uv run python main.py --config config.yaml
```

构建脚本仅清理已解析校验的 `src/presentation/static/app/`，保留相邻 Ace 文件。产物包含 manifest 和按路由分块的哈希文件；提交源码、锁文件和产物后，部署端可直接启动 Python 服务。`pyproject.toml` 是版本来源，页面从 bootstrap 读取实际版本。

HTML 和 bootstrap 使用 `no-store`，manifest 使用 `no-cache`，哈希资源使用长期不可变缓存。部署使用同一版本的 Python 代码、manifest 和全部资源，浏览器刷新获取新入口。生产入口支持既有深链接，未知 `/api/*`、`/v1/*` 和页面路径遵循 Flask 路由结果。

独立目录验证包括重新安装依赖、构建、比对产物、运行 Flask 入口/认证/Agent 回归，并检查生产资源不包含组件展示页。构建后的页面运行不需要 Node.js。
