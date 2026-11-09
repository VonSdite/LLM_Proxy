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
| 列表页标题、搜索工具与批量操作 | UiPageHeader、UiListToolbar、UiSearchInput、UiSelectionBar |
| 操作、字段标签和错误 | UiButton、UiIconButton、UiField |
| 文本、数值、密码 | UiInput、UiNumberInput、UiSecretInput |
| 固定选择、搜索/手填、多选筛选 | UiSelect、UiCombobox、UiMultiSelect |
| 开关、复选、三态 | UiSwitch、UiCheckbox、UiTriState |
| 分页、表格、拖动列表 | UiPagination、UiTable、UiSortableList |
| 简短提示、帮助 | UiTooltip、UiHelpPopover |
| 对话框、操作确认 | UiDialog、UiConfirmPopover |
| 操作菜单 | UiDropdownMenu |
| 左侧抽屉 | UiDialog 的 `placement="left"` |
| 状态、消息、空数据 | UiBadge、UiStatus、UiAlert、UiEmptyState、notify |
| 文件编辑 | UiCodeEditor；业务使用 ConfigFileEditor |

基础组件通过 `@/components/ui` 导入。业务页面的 CSS 负责布局，颜色、边框、阴影、焦点和层级来自 `styles/tokens.css`。控件视觉和交互在组件内统一维护。`/dev/components` 使用正式组件，生产构建不包含该页面。

文字层级使用 `--heading`、`--text`、`--label` 和 `--muted`，分别对应标题、正文与输入值、字段标签和次要信息。`--weight-heading`、`--weight-emphasis`、`--weight-label` 和 `--weight-option` 统一控制标题、重点文字、标签与表头、数值建议的字重。浅色主题采用中性浅灰背景、灰蓝文字、500 标题与重点字重和 400 标签字重；深色主题保留各自的字重与颜色。占位文字透明度通过 `--placeholder-opacity` 管理，浅色主题使用完整次要文字颜色。正文、标签、帮助、链接及常用背景组合的文字对比度至少为 4.5:1。

Provider、OAuth、模型映射、API Key 和用户页使用 `UiPageHeader` 放置标题与可选分区。`UiListToolbar` 的 `actions` 插槽在列表左侧按新增、状态筛选、导入、导出排列，状态筛选统一使用分段控件；搜索、刷新与辅助工具位于右侧。Provider、OAuth、模型映射与组件展示页遵循相同顺序，空间不足时按顺序换行。OAuth 使用 `wideFilters`，内容区域不超过 1360px 时左侧操作区独立成行，搜索与刷新等工具位于下一行。`filters` 插槽承载用户页的白名单开关和 OAuth 全选等辅助控件，在内容区域不超过 1120px 时独立成行。700px 及以下的操作与搜索分别成行，按钮可自动换行。`UiSearchInput` 统一搜索图标与清空按钮，清空后焦点留在输入框。

`UiSelectionBar` 仅在当前操作范围内存在选择时显示，选中数量旁紧邻小号 × 图标，悬停或键盘聚焦提示「清空选择」，批量工具排列在其后。工具栏使用柔和底色与普通边框，操作使用标准尺寸的次级按钮，删除入口使用 `UiButton variant="danger-outline"`，清空图标使用次要文字色。数量与清空入口始终保持一组，内容区域不超过 560px 时，批量工具在下一行自动换行。清空选择时，焦点可返回仍存在的原勾选框。基础布局组件不持有业务数据或发送请求。

OAuth 状态筛选依次显示可用、额度用完、认证失败、已禁用和全部；全部包含其他不可用状态的账号。添加账号弹窗的授权链接右侧提供“复制链接”和“打开链接”，窄屏时操作在链接下方右对齐，生成链接入口独立在上方。

Provider 与 OAuth 批量操作跟随所选记录的已确认状态：全启用仅显示“禁用”，全禁用仅显示“启用”，混合选择同时显示两项。启停请求只包含需要改变状态的所选记录。Provider 成功后清空选择，失败保留选择并显示错误；OAuth 保留仍在当前筛选范围内的选择，逐项失败信息可用于重试。单项启停等待期间，批量按钮保留原状态并禁用；响应成功后再更新可用操作。模型映射、用户和 Provider 模型编辑使用相同的批量栏、按钮尺寸及删除入口样式。

Provider、用户、模型映射和 OAuth 的导入、导出入口常驻工具栏；“导出”使用状态与搜索条件匹配的全部结果，悬停或聚焦显示范围与数量，分页列表包含全部匹配页。“导出所选”只处理明确勾选的记录。空结果与搜索等待期间的导出不可用。用户导出通过 `keyword` 使用与用户列表相同的服务端搜索规则；所选导出显式传递 ID。下载完成保留列表节点、选择与滚动位置。OAuth 模型目录通过工具栏直接打开，导入保留原生多文件选择。文件导入、导出使用 `FileInput`、`FileOutput` 图标，页面入口与实际动作具有相同含义。

`UiSelect` 与 `UiCombobox` 的 `option` 插槽接收 `{ option }`，可展示模型名、数值和来源；选中控件显示 `label`，保存值使用 `value`。桌面控件为 32/36px，`pointer: coarse` 下常用控件点击区域至少为 44px，复选框与开关的图形仍保持紧凑。

Reka 的 Portal、Presence 等包装层与实际 DOM 的 scope 属性可能不同。浮层和通知最外层使用唯一的 `ui-*` 类及 `:global()` 选择器，内部布局继续采用 scoped 样式。浏览器检查包括实际背景、定位与层级。需要保留字段或说明 ID 时，使用 `as-child` 并将 ID 放到原生元素；标签和 `aria-describedby` 始终关联实际控件。

页面组合 `features` 的业务组件，业务通过 `api` 访问服务。Pinia 保存会话、功能开关和主题；表单草稿、机器、分页和测试结果属于对应页面。主题兼容 `localStorage.theme`，默认深色。表格列宽按 `storageKey` 存入 `table-widths:*`，凭据不写入本地存储。

`app/AppShell.vue` 负责桌面侧栏、收起状态、窄屏菜单和内容区域；`app/AppNavigation.vue` 统一提供导航项、功能门控、当前页面标记、主题切换、用户名和退出。桌面侧栏宽度为 216px，收起后为 72px。901–1280px 默认收起为图标栏，可手动展开；区间内缩放与切换页面保留当前选择，再次进入该区间时自动收起。1280px 以上恢复本次会话中宽屏的手动选择，初始为展开。900px 及以下使用左侧抽屉，选择页面或回到桌面尺寸后关闭。断点通过 `matchMedia` 的变化事件响应，组件卸载时移除监听。

导航区独立滚动，收起按钮位于侧栏顶部。底部账户入口与主题按钮共用一行，账户入口展示用户名首字母和用户名，点击后通过 `UiDropdownMenu` 向上展示退出操作；长用户名省略显示，收起侧栏时保留首字母入口。抽屉使用公共对话框的 Escape、遮罩关闭和焦点恢复能力。Agent 机器列表按内容区域宽度排列，980px 及以下显示在配置表单上方。

内容区域使用侧栏之外的可用宽度，桌面两侧内边距为 24px，窄屏为 16px。`UiTable` 的顺序和勾选列保持固定宽度，`TableColumn.fixed` 使指定列保持设定宽度，其余列分配剩余空间。`TableColumn.grow` 指定额外空间的分配权重，默认权重为列宽；`maxWidth` 限定列宽上限，达到上限后的剩余空间分配给未设上限的弹性列。列宽偏好保存在 `table-widths:<storageKey>`。`reorderable` 决定是否显示顺序列，`reorderDisabled` 决定当前是否允许排序；等待请求和搜索期间保留列结构。顺序列统一为桌面 40px、触屏 52px，相邻勾选列为桌面 32px、触屏 44px；把手与选择框的中心间距分别为 32px 和 44px。表头视觉留空并保留“顺序”的可访问名称。表格单元格垂直居中，窄屏表格在容器内部滚动，横向标签保留触摸与键盘导航。

Provider、模型映射、Provider 模型草稿和映射目标列表共用 `UiDragHandle` 与 `useRowDrag`。界面只显示把手；桌面操作目标为 32px，触屏为 44px。拖动在目标行上方或下方显示插入线，列表末尾下方 32px 是拖动期间的有效落点，该区域不占用正常布局空间。`UiSortableList` 的行间空隙同样接受拖入。触屏拖动经过移动阈值后开始，靠近滚动区域边缘时自动滚动；松手提交，取消手势保留原顺序。聚焦把手后可用上下方向键移动，Home / End 定位首尾；等待保存时保留把手焦点，行移动后恢复焦点。外部拖入、原位置落下及禁用期间不提交排序。

Provider 和模型映射使用 `reorderGroup` 限定启用、禁用分组。全部视图中，拖到另一组区域会定位到当前组的边界，顺序始终符合后端的启用项在前规则。排序接口继续接收完整列表，筛选内调整保留其余记录。`sort.css` 统一插入线、拖动状态和末尾落点样式。

拖动落点计算通过 `requestAnimationFrame` 合并同一帧的移动事件；候选行索引在拖动期间复用，列表更新时失效。按垂直行序二分读取实时位置，滚动和窗口缩放后刷新落点。插入线属性由排序模块直接维护，仅修改旧、新目标行，不触发表格业务单元格重渲染。松手时按最终坐标同步计算并提交一次排序；取消或卸载清理插入线、帧任务和事件监听。

`features/providers/ProviderTable.vue` 组合公共表格和按钮，提供 Provider 单元格展示。名称使用 `UiButton variant="link"` 的统一链接色与 16px 重点文字，深浅主题分别采用各自的 `--link` 语义色。名称单行展示，点击或按 Enter 打开对应 Provider 的编辑弹窗，关闭后焦点返回名称。名称入口与操作列编辑按钮共用忙碌状态和编辑流程。地址、上游协议、模型和认证使用 14px 正文字体。名称、上游协议、认证、开关与操作列保持紧凑宽度，地址与模型按 5:6 分配额外空间，地址列上限为 720px。长地址在列内自动换行、完整展示；名称在可用宽度不足时省略，`UiTooltip` 在悬停或聚焦时展示全文。模型列最多预览六个模型 ID，使用 `UiBadge size="md"` 的灰蓝色圆角标签，背景和文字由 `tag-background`、`tag-selected-background`、`tag-text` 语义色提供；选中行保留标签与行背景之间的区分。标签按列宽自动换行，长模型 ID 在标签内省略，悬停展示全文，数量入口在下方左对齐。认证组与模型详情使用 `UiButton` 的 `link` 样式，其零水平内边距与普通认证文字对齐。每个单元格的内容作为整体在行内垂直居中。选中行在悬停时保留选中色。

Provider 接口的 `hidden_model_list` 为可选字段，省略时表示没有隐藏模型。模型详情和编辑器使用相同语义；详情支持搜索、复制完整模型 ID、鼠标与键盘打开及关闭后恢复焦点。Provider 单条删除确认显示“是否删除？”，操作按钮的可访问名称仍包含对应 Provider。`UiConfirmPopover` 的确认按钮使用 `UiButton variant="danger-solid"`，由 `danger-action`、`danger-action-hover` 和 `on-danger` 语义色提供实心红底、白色文字与悬停反馈。

系统设置在同一页平铺基础设置、客户端 IP、API Key、模型映射、OAuth 网络和日志与调试。每组使用独立面板与保存入口；API Key 和模型映射在宽屏并排，700px 及以下上下排列。各保存分组持有当前草稿与已确认快照。只有变更过的分组可保存；保存期间该组字段禁用，其他分组仍可编辑。成功响应只更新对应分组；错误在分组内显示。重新读取在存在草稿时显示丢弃确认，读取失败保留草稿。

模型权限使用 ModelPermissionPicker，API Key 使用 ApiKeyField，代理设置使用 ProxySettings，路径使用 ConfigPathRow，文件使用 ConfigFileEditor。Agent 配置解释与清空规则集中在 `features/agents/configuration.ts`。

`UiField` 的普通模式关联标签、错误和控件；复合字段使用 `group`，内部输入分别标注名称。`inline` 模式将标签、帮助与开关放在同一行，标签居左、开关居右，使用统一的柔和底色与圆角；点击标签也可切换。数值输入的 `update:modelValue` / `commit` 在失焦或 Enter 且数值变化时触发，候选选择立即提交；输入期间保留原草稿。业务使用 `validity-change` 或表单原生校验阻止非法值提交。

Provider 编辑表单在桌面按三列排列，900px 及以下为两列，600px 及以下为单列。连接方式与自定义代理地址属于同一个网格单元，自定义模式下选择器宽度为 108px，使用 `UiSelect.compact` 缩小水平内边距与箭头间距，地址输入、认证按钮与右侧问号在同一行展开；认证按钮打开锚定浮窗填写用户名与密码，表单保持一行；请求超时与最大尝试次数保持原列。证书校验、强制上游流式和安全脱敏使用 `UiField inline` 配合 `UiSwitch`。列表的 API 地址下方通过 `UiBadge` 展示已启用的三项功能及配置的 Hook，长 Hook 路径在徽标内省略，悬停或键盘聚焦可查看完整路径。地址与徽标作为整体在单元格内垂直居中。

列表以持久业务 ID 为 key，新行使用独立临时 ID。`useRequest` 取消被替代的读取并丢弃迟到结果；`useAction` 串行执行操作、集中显示错误，回调接收取消信号；自定义流程使用 `useLifetime` 并在异步步骤后检查信号。服务端已经完成的写入仍然生效，取消请求不提供事务回滚。

`useToggle` 分离开关的临时显示与已确认数据，按记录互斥，不同记录可独立提交。`UiSwitch.loading` 显示局部等待并保留键盘焦点，等待期间忽略重复点击；失败时恢复已确认值并通过通知反馈。Provider、模型映射、用户、API Key 和 OAuth 单项启停直接更新对应记录；用户与 API Key 通过 PUT 发送显式布尔值。映射及其目标共用记录锁。认证组运行状态在写入后读取服务端快照，同组操作串行。

`useRequest.patch` 将已确认写入合并到当前数据；已有读取仍在途中时，同一更新也合并到该读取的响应。之后发起的新读取以服务器结果为准。`usePagedList` 保留仍在当前结果内的选择，页码、排序和搜索变化清空选择。OAuth 自动同步在开关操作期间暂停，单项启停保留其他账号的开关操作能力。

编辑浮窗使用 `UiPopover`，标题、关闭、焦点、定位与主题样式由基础组件提供，组件展示页提供示例。编辑浮窗、问号说明、确认气泡与普通悬停提示共用 `UiPopoverArrow`，尾巴随碰撞定位指向触发控件，颜色跟随明暗主题；可滚动内容位于内部容器，尾巴保持在滚动区域之外。帮助统一使用 `UiHelpPopover`，支持悬停、键盘聚焦、移入内容、触屏点按和 Escape。显式点按会取消尚未触发的悬停计时。长说明限制在视口可用高度内，并在弹层内部滚动；段落与列表间距由共享组件提供。每个入口具有对应功能的可访问名称。

`UiHelpPopover.text`、`UiField.help` 和 `TableColumn.help` 接收字符串或只读字符串数组：字符串显示为段落，数组统一显示为无序列表，每项说明一个重点。复杂排版和动态内容使用内容插槽，字段使用 `#help`。`TableColumn.help` 在表头追加独立问号，与排序按钮和列宽手柄分别操作。表格新增问号时同时检查标题宽度及触屏点击区域。

`UiPopoverArrow` 的底部填色延伸进浮层，覆盖边框和轮廓阴影的接缝；带边框版本仅沿尾巴斜边描线，底边保持开放。填色与描边分开绘制，随定位方向一同旋转。

`UiField.labelHidden` 保留屏幕阅读器可识别的标签关联，视觉上省略标签行。自定义代理使用紧凑的地址行，认证按钮通过 `UiPopover` 打开用户名、密码编辑浮窗，已设置认证时高亮，未设置时保留边框。打开和收起浮窗不改变数据；输入实时更新外层表单草稿，「完成」只收起浮窗，外层保存提交配置。密码复用 `UiSecretInput`，默认隐藏且支持眼睛切换。账号密码按原文填写，由前端统一编码一次后组成现有代理 URL；字面 `%40` 保存为 `%2540`。已有 URL 和粘贴的完整 URL 自动拆分，凭据解码后回填；未编辑或恢复原值时保持原 URL 写法。「清除」清空用户名与密码，提交的 URL 不含凭据。浮窗关闭后重新打开保留当前草稿。Provider、OAuth 和 SSH 共用此交互，输入错误保留字段关联和红色标记。OAuth 网络与 SSH 机器的自定义代理使用所在表单的整行宽度，地址和认证字段保留充分的输入空间。

`UiTable` 的 `header-<列 key>` 插槽提供列操作入口，保留独立问号与列宽手柄。`addLabel` 在表格末尾提供整行添加入口，`addDisabled` 控制禁用，点击触发 `add`；空表也展示添加行，该行不参与选择、排序与统计。Provider 模型表格在添加后聚焦新模型输入框，每行眼睛图标控制目录可见性；表头眼睛在全部可见时执行全部隐藏，混合或全部隐藏时执行全部显示。修改随 Provider 表单保存。

Provider 拉取模型后，候选列表默认勾选当前草稿中已有的模型。「确定」按候选的最终勾选状态更新草稿：新勾选的模型追加，取消勾选的已有模型移除，本次候选之外的模型保留。保留项的顺序、显示状态和测试结果保持一致；搜索与筛选全选只影响对应候选。外层 Provider 保存提交配置，选择弹窗取消不更新草稿。

Provider 模型测试使用“结果、首字延迟、TPS”独立列，状态后没有问号。操作列完整展示错误文本，保留原文换行，长文本自动折行并撑开当前行；批量入口使用“测试”和“删除”。计时口径、实际请求参数与适用范围见 [Provider 模型测试口径](provider-model-testing.md)。

Auth Group 名称与模型映射的对外模型 ID 使用 `UiButton variant="link"` 的公共链接色。点击名称或按 Enter 打开对应编辑弹窗，关闭后焦点返回名称；入口与操作列编辑按钮共用编辑流程及忙碌禁用规则。模型 ID 的独立复制按钮保持可用。

使用 Auth Group 时，「测试 Auth Entry」位于模型区顶部操作按钮左侧，标签、说明问号与选择器横向排列；窄屏按可用空间换行。所选凭据用于当前表单的拉取模型与模型测试。

模型测试按稳定行 ID 关联请求与结果，仅正在请求的行禁用模型 ID 输入、测试和删除；其他行可编辑、移除或独立测试，拖动排序保持结果归属。批量测试按启动时的行顺序依次请求，等待行仍可修改，已移除或正在独立测试的行跳过。停止测试、连接配置变化与组件卸载取消当前请求及等待队列；旧响应不覆盖后续测试状态。整理在测试期间不可用。

`features/providers/modelTestHelp.ts` 集中定义 TTFT 和 TPS 说明，Provider 表头与模型映射测试详情共同使用。TTFT 从请求发起计至首个有效正文或思考输出；TPS 使用生成区间公式，并列明分块误差、隐藏思考及无法计算时的占位行为。

Provider 及认证组的说明按选项、行为、作用范围和留空规则组织。认证方式分别解释 API Key 与 Auth Group；代理、超时、重试、证书校验、流式响应、脱敏、模型操作、认证组限额与运行状态采用列表分项。协议示例按名称、路径、请求字段分行，保留默认值、计数口径、状态影响及必要示例。

说明先解释作用和影响范围，再给出单位、留空行为、默认值和必要示例。服务端与浏览器、本地配置与上游限制、目录可见性与调用权限分别说明。涉及客户端版本的语义标明核对版本；后端业务规则以当前实现为准。`ProxySettings` 按 Provider、OAuth、SSH 场景说明连接来源，`DesensitizationHelp` 统一说明识别范围与各开关的叠加关系。迁移核对来源与逐页覆盖记录见验收文档的“帮助说明复核”。

对话框负责焦点约束、Escape、遮罩与关闭后恢复。初次打开优先聚焦带 `autofocus` 的输入框或安全操作；没有指定目标时聚焦弹窗本身，关闭按钮保持常态。键盘导航到按钮时显示焦点提示。`UiTooltip` 由真实悬停或键盘聚焦触发；鼠标关闭弹窗后恢复焦点保持提示关闭。

编辑弹窗通过 `UiDialog.dirty` 声明草稿状态。Provider、Auth Group、模型映射、API Key、用户与批量权限、SSH 机器、Agent 路径、OAuth 回调、JSON 导入和模型选择均有关闭保护。`useDraftChanges` 比较本次打开时的初始草稿；恢复原值后无修改，查看密钥、筛选和测试结果等展示状态不计入草稿。模型和条目列表保留原始输入参与比较；`UiNumberInput` 通过弹窗内的注册表报告尚未失焦或无效的数值文本。

点击外侧与 Escape 经过关闭检查；右上角关闭和页脚取消直接放弃草稿并关闭，页脚通过 `#footer="{ close }"` 调用 `close()`。关闭检查先提交当前输入框的最新值，再比较草稿；数值恢复原值或使用等价数字格式时直接关闭。存在修改时显示“继续编辑”“不保存”和“保存并关闭”，取消提示保留草稿；确认只关闭当前弹窗。`save` 回调复用原表单的校验与保存，`saveLabel` 对应导入、模型添加和 OAuth 登录等具体操作。保存期间关闭入口不可用，成功保存由业务直接关闭，校验或保存失败保留原表单、草稿与错误信息。文件编辑器通过 `beforeClose` 为外侧点击与 Escape 提供相同的三项选择，页脚关闭和右上角关闭直接放弃草稿。Ace 从本地 vendor 按需加载，组件卸载时销毁实例。

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

## 认证组导入导出与文本编辑器

Auth Groups 工具栏提供“导入”“导出”。导出范围为当前搜索条件匹配的全部认证组，空结果时禁用；悬停提示包含范围及数量。下载文件为 `llm-proxy-auth-groups.json`，格式为 `version: 1`、`kind: llm_proxy.auth_groups` 和 `auth_groups` 列表，内容包含完整认证请求头、组策略及条目限额。

`POST /api/auth-groups/export` 接收 `names` 列表。`POST /api/auth-groups/import` 接收包含 `auth_groups` 列表的 JSON 对象，支持认证组导出文件及 Provider 导出文件中的认证组配置。同名组保留已有设置和同 ID 凭据，补入缺少的条目；所有组通过校验后一次写回并重载。组级导入导出范围限定为配置与凭据，Provider、运行时统计、冷却和用量记录由各自原有流程管理。两个接口使用管理会话认证，业务由 `AuthGroupService` 编排，合并规则与 Provider 导入共用。

`UiCodeEditor` 使用 15px 等宽字体，行号左侧内边距为 6px，保留折叠按钮。Ace 根据行号位数与内边距计算栏宽及正文位置。Provider 导入、认证组 YAML、模型映射导入及配置文件编辑使用同一规则，明暗主题保持语法高亮。

认证组编辑侧栏每行包含条目名称、启用开关与移除图标，添加入口位于列表末尾。启停只更新该条目草稿，移除当前条目后选择相邻条目。窄屏条目列表位于表单上方，保持名称与操作同排。右侧表单从 Entry ID 开始。

“编辑 / 导入”通过 `TextImportDialog` 展示当前条目的 YAML，支持选择 YAML / JSON 文件、粘贴和直接编辑；“确定”调用条目解析接口并整体更新表单，保存 Auth Group 后写入配置。该组件共用文件选择、Ace、错误显示、请求取消和草稿关闭保护；`JsonImportDialog` 在同一组件上提供 JSON 校验及既有导入接口。取消或关闭按钮直接放弃当前窗口草稿，外侧点击与 Escape 在内容变化时提示。

认证组运行状态使用 `UiDialog placement="top"`，顶部间距随窗口高度在 24–96px 之间变化，内容在弹窗内部滚动，刷新保持顶部位置。刷新入口使用公共图标按钮。表格直接展示请求用量和上限，以及最近 HTTP 状态码、错误类型、完整错误信息和更新时间。手动禁用通过状态徽标及开关体现，其他禁用原因与最近 HTTP 状态码去重后展示。运行状态与历史请求结果使用各自列说明，数据统一来自原运行状态接口。

Auth Groups 使用 `UiTable` 勾选列和 `UiSelectionBar` 批量操作栏，支持全选当前搜索结果、清空选择、导出所选和删除所选。认证组与 Provider 选择独立，搜索或切换标签清空选择，刷新后剔除已不存在的记录。`POST /api/auth-groups/batch` 接收 `action: delete` 与 `names`，在配置写锁内校验所有名称及 Provider 引用，通过后一次写回并重载；存在引用或无效名称时整批保留。删除失败保留当前选择，成功后移除已删除项的选择。

认证条目侧栏使用 `UiSelectableRow`，选中行以统一底色覆盖名称和操作区域，名称使用链接色。名称按钮保持透明边框，悬停反馈作用于整行；开关和移除动作独立于条目选择。组件展示页包含对应示例。

`UiConfirmPopover` 使用独立的标题、说明与操作区，支持字符串或无序列表形式的 `description`。说明采用次级文字色，操作按钮靠右，Portal 内层使用全局唯一类名保证布局生效。运行状态重置确认以两条说明分别展示状态清除和用量清零，保留红色确认按钮和公共气泡尾巴。
