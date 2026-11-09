# 前端开发约束

本目录使用 Vue 3 Composition API、TypeScript strict 和 Vite。运行环境为 Node.js 24，依赖版本由 package-lock.json 固定。

- 页面组合业务组件；features 管理业务状态；api 处理请求；components/ui 提供无业务依赖的基础控件。
- 基础组件通过 `@/components/ui` 导入；Reka UI、TanStack Table 和 Lucide 的直接导入限定在基础组件目录。
- 排序统一使用 `UiTable` 或 `UiSortableList`，内部共用 `UiDragHandle`。界面只显示拖动把手，顺序列表头视觉留空；支持首尾落点、触屏拖动及键盘移动。
- 批量操作统一使用 `UiSelectionBar` 和标准尺寸按钮，删除入口使用 `danger-outline`，最终确认使用 `danger-solid`。启停入口按所选记录的已确认状态显示，请求只包含需要改变状态的记录。
- 业务模块使用 api 层，fetch、上传、下载和会话错误处理集中在 api/client.ts。
- 页面样式只处理布局。控件样式属于基础组件。颜色、阴影、焦点和浮层层级使用 styles/tokens.css 中的语义变量。
- 深浅主题覆盖页面、浮层和 Ace。主题偏好使用 localStorage.theme，默认 dark。
- 帮助使用 UiHelpPopover，简短提示使用 UiTooltip；弹窗使用 UiDialog 并支持关闭钩子。
- 编辑弹窗通过 UiDialog 的 dirty 属性保护外侧点击和 Escape 关闭时的未保存草稿。取消按钮使用 footer 插槽的 close()，与右上角关闭按钮一样直接放弃草稿并关闭；保存期间均不可关闭。save 回调复用原表单的校验与保存。外侧或 Escape 关闭前提交当前输入并比较本次打开的初始值，恢复原值即无修改；成功保存直接关闭。确认提供继续编辑、不保存和保存并关闭，配置文件编辑器通过 beforeClose 提供相同选择。
- Reka 的 Portal、Presence 等包装层可能不传递 Vue scope 属性。浮层和通知的最外层使用唯一的 `ui-*` 类及 `:global()` 样式，并在浏览器检查实际背景、定位和层级。标签和说明的 ID 由 `as-child` 的原生元素持有。
- 固定选择和搜索选择均提供 `option` 插槽，接收 `{ option }`；选中值仍显示选项 label。触屏尺寸通过 `pointer: coarse` 和公共控件统一设置。
- 业务列表使用稳定 ID。请求关联当前草稿，组件卸载时取消请求与定时器。
- 读取使用 useRequest，串行操作使用 useAction 并将回调的 signal 传入 API；自定义流程使用 useLifetime，在 await 后检查 aborted。
- Agent 数值输入使用 commit 事件。原文件精确 Token 数值与百分比显示分开存储。
- 注释使用中文，日志使用英文；文本为 UTF-8 无 BOM、LF。
- `src/dev/ComponentsPage.vue` 展示正式组件，路由仅在开发和测试构建可达。
- 功能对应及验证记录位于仓库根目录的 `docs/frontend-migration-audit.md`，组件选择和部署步骤位于 `docs/frontend-development.md`。

交付检查：`npm ci`、`npm run lint`、`npm run typecheck`、`npm run test:unit`、`npm run build`、`npm run test:e2e`、`npm run test:visual`。后端及模型表检查采用仓库根目录的命令。
