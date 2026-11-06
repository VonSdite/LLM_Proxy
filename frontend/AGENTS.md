# 前端开发约束

本目录使用 Vue 3 Composition API、TypeScript strict 和 Vite。运行环境为 Node.js 24，依赖版本由 package-lock.json 固定。

- 页面组合业务组件；features 管理业务状态；api 处理请求；components/ui 提供无业务依赖的基础控件。
- 基础组件通过 `@/components/ui` 导入；Reka UI、TanStack Table 和 Lucide 的直接导入限定在基础组件目录。
- 业务模块使用 api 层，fetch、上传、下载和会话错误处理集中在 api/client.ts。
- 页面样式只处理布局。控件样式属于基础组件。颜色、阴影、焦点和浮层层级使用 styles/tokens.css 中的语义变量。
- 深浅主题覆盖页面、浮层和 Ace。主题偏好使用 localStorage.theme，默认 dark。
- 帮助使用 UiHelpPopover，简短提示使用 UiTooltip；弹窗使用 UiDialog 并支持关闭钩子。
- Reka 的 Portal、Presence 等包装层可能不传递 Vue scope 属性。浮层和通知的最外层使用唯一的 `ui-*` 类及 `:global()` 样式，并在浏览器检查实际背景、定位和层级。标签和说明的 ID 由 `as-child` 的原生元素持有。
- 固定选择和搜索选择均提供 `option` 插槽，接收 `{ option }`；选中值仍显示选项 label。触屏尺寸通过 `pointer: coarse` 和公共控件统一设置。
- 业务列表使用稳定 ID。请求关联当前草稿，组件卸载时取消请求与定时器。
- 读取使用 useRequest，串行操作使用 useAction 并将回调的 signal 传入 API；自定义流程使用 useLifetime，在 await 后检查 aborted。
- Agent 数值输入使用 commit 事件。原文件精确 Token 数值与百分比显示分开存储。
- 注释使用中文，日志使用英文；文本为 UTF-8 无 BOM、LF。
- `src/dev/ComponentsPage.vue` 展示正式组件，路由仅在开发和测试构建可达。
- 功能对应及验证记录位于仓库根目录的 `docs/frontend-migration-audit.md`，组件选择和部署步骤位于 `docs/frontend-development.md`。

交付检查：`npm ci`、`npm run lint`、`npm run typecheck`、`npm run test:unit`、`npm run build`、`npm run test:e2e`、`npm run test:visual`。后端及模型表检查采用仓库根目录的命令。
