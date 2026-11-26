# 前端开发与部署

管理界面使用 Vue 3 Composition API、TypeScript strict、Vite 和 Vue Router。Flask 为页面提供同源入口与生产资源，Node.js 仅用于开发、测试和构建。运行时职责与请求链路见 [4+1 架构](architecture-4plus1.md)，目录约束见 [frontend/AGENTS.md](../frontend/AGENTS.md)。

## 1. 开发环境

使用 Node.js 24、npm 11、Python 3.12+ 和 uv。前端依赖由 `frontend/package-lock.json` 固定。

启动后端：

```text
uv sync
uv run python main.py --config config.yaml
```

另一个终端启动 Vite：

```text
npm --prefix frontend ci
npm --prefix frontend run dev
```

开发地址为 `http://127.0.0.1:5173`。Vite 的 `/api`、`/static/vendor` 和 `/logout` 代理默认指向 `http://127.0.0.1:8080`。后端端口不同时，在 PowerShell 7 中设置：

```powershell
$env:LLM_PROXY_DEV_URL = "http://127.0.0.1:9090"
npm --prefix frontend run dev
```

管理 API 代理保留浏览器 Host / Origin，Agent 接口继续校验同源请求、浏览器会话和 `X-Agent-Config: 1`。开发期间使用独立配置和数据目录。

## 2. 目录与依赖边界

```text
frontend/
  src/
    app/                 应用外壳、页面注册表、路由和导航
    api/                 API 客户端、请求类型和错误处理
    components/ui/       无业务依赖的共享控件
    features/            领域组件、状态和组合函数
    pages/               路由页面
    stores/              会话、页面注册表、功能开关和主题
    styles/              设计变量与全局样式
    dev/                 开发及测试模式的组件展示页
  tests/
    unit/                Vitest 单元与组件测试
    e2e/                 Playwright 浏览器交互测试
extensions/              本地管理端扩展包（前端页面与后端 Python）
src/presentation/
  templates/app.html     Flask 页面入口
  static/app/            生产构建资源与 manifest
```

- 业务请求统一经过 `api` 层，基础组件通过 `@/components/ui` 导入。
- 页面注册表位于 `app/page-contract.ts`、`app/builtin-pages.ts` 和 `app/page-registry.ts`，内置与扩展页面共用路由、标题和侧边栏描述。
- 凭据不写入 localStorage；`/dev/components` 仅在开发和测试模式可达。

组件、状态、主题和交互约束以 `frontend/AGENTS.md` 及共享组件实现为准。

全局键盘焦点样式通过 `:where(...)` 提供低优先级的默认描边，组件的行高亮、内描边和焦点阴影优先。`UiSelect` 与 `UiDropdownMenu` 的当前选项使用整行背景提示焦点，`UiCombobox` 的输入焦点由容器边框和阴影提示；普通按钮与链接保留默认焦点描边。鼠标、键盘和触屏共用这些样式，弹层内的焦点提示保持在可见区域。

`UiTable` 的 `adaptive` 属性启用按容器分配列宽：`width` 为首选值，`minWidth` / `maxWidth` 为伸缩边界，`grow` 为分配权重；`fixed` 或 `grow: 0` 的列保留首选宽度。表头保持单行，控件实际宽度与内边距参与计算列宽下限，伸缩边界至少容纳完整表头。列配置 `fitContent: true` 时，组件按当前已加载行和合计行的实际渲染内容测量不换行宽度，并计入单元格内边距；该宽度与表头下限共同确定自动宽度上限。配合 `grow: 0` 时，内容宽度同时作为可读下限；弹性列按内容宽度与下限的差值分配伸缩空间。

手动调整的列在上下限内优先保持宽度，支持拉宽到自动内容宽度以上；其余弹性列分配剩余空间，全部达到下限时使用内部横向滚动。表格宽度使用列宽合计，表头、数据行和合计行保持对齐；全部列达到自动上限后保持各列宽度。`storageKey` 按表格保存手动列宽，已保存值受可读下限约束，双击边界清除对应列的手动值；方向键每次调整 16px。`compact` 属性提供紧凑的单元格与表头间距。统计页的全部用量表格和请求明细启用这两个属性，数值列的下限同时由完整显示值、单位与合计计算。用户名与 IP 共用各自的列配置；IP 使用 `fitContent` 与 `grow: 0` 保持最长地址单行可读，请求、映射与响应模型分别使用 `fitContent` 按本列内容伸缩。Key 等弹性文本列按配置分配空间，IP、用户汇总由数值列分配剩余空间。

`StatisticsPage` 保存趋势时间粒度并通过概览请求的 `interval` 传递；切换粒度保留已应用筛选与当前趋势指标，取消前一次读取。日期范围变化时同步选择当前范围最细的粒度，并合并为一次概览请求；修改其他筛选保留手动选择。`StatisticsOverview` 仅提供当前日期范围支持的粒度：单日提供 5 分钟、小时、天，2 至 7 天提供小时、天，超过 7 天仅提供天；根据返回的 `series_interval` 显示实际粒度，根据 `series_partial` 说明历史覆盖范围。`UiTrendChart.formatLabel` 格式化横轴端点，悬浮与键盘聚焦提示保留原始完整时间。

## 3. 管理端页面扩展

`extensions/<id>/frontend/index.ts` 默认导出扩展页面定义。扩展宿主自动生成 `/extensions/<id>` 路由和侧边栏入口，页面组件通过异步加载函数进入独立构建分块。扩展目录名与页面声明 ID 一致，Vite 开发服务和构建清单读取同一扩展根目录。

后端动作入口为 `extensions/<id>/extension.py`，宿主将其 Blueprint 挂载到 `/api/extensions/<id>/` 并应用管理端登录校验。页面与后端位于同一个扩展包内。

页面协议、上下文、生命周期、动作接口、长任务和安全边界见 [管理端扩展开发](extension-development.md)。

## 4. 检查与测试

```text
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test:unit
npm --prefix frontend run build
uv run python -m unittest discover -s tests
uv run python scripts/check_agent_model_limits.py
```

浏览器交互测试：

```text
npm --prefix frontend run test:e2e
```

Playwright 使用 Chromium、中文环境、上海时区和减少动画设置。测试服务器以 test 模式启动，测试通过独立 API fixture 拦截 `/api/*` 请求，不写入真实用户配置、SSH 机器或数据库。未声明请求作为失败证据记录。

已有获准的 Chrome CDP 实例时，可在 PowerShell 7 中指定地址；每个用例仍创建独立浏览器上下文：

```powershell
$env:PLAYWRIGHT_CDP_ENDPOINT = "http://127.0.0.1:9222"
npm --prefix frontend run test:e2e
```

未指定 CDP 时使用 Playwright 管理的 Chromium，可通过 `npx --prefix frontend playwright install chromium` 安装对应浏览器。`--list` 仅检查用例发现，不执行浏览器。

失败截图和 trace 位于 `frontend/test-results/`，HTML 报告位于 `frontend/playwright-report/`。这些输出以及临时浏览器、缓存和调试文件不参与版本控制；按仓库约定将可控临时输出放到根目录 `tmp/`。

组件和 jsdom 测试覆盖状态逻辑，浏览器测试覆盖实际交互，人工检查覆盖视觉质量。

## 5. 构建与部署

```text
npm --prefix frontend ci
npm --prefix frontend run build
uv run python main.py --config config.yaml
```

构建脚本仅清理已解析校验的 `src/presentation/static/app/`，保留相邻 Ace 文件。产物包含 manifest 和按路由分块的哈希文件；源码、锁文件与产物一同提交后，部署端可直接启动 Python 服务。`pyproject.toml` 是版本来源，页面从 `/api/ui/bootstrap` 读取实际版本。

HTML 和 bootstrap 使用 `no-store`，manifest 使用 `no-cache`，哈希资源使用长期不可变缓存。部署使用同一版本的 Python 代码、manifest 和全部资源。生产入口支持既有深链接，未知 `/api/*`、`/v1/*` 和页面路径遵循 Flask 路由结果。构建后的页面运行不需要 Node.js。
