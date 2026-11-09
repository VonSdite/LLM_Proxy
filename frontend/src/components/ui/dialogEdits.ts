import type { InjectionKey } from 'vue'

/** 数值控件尚未失焦提交的文本也属于当前弹窗的草稿。 */
export const dialogPendingEdits: InjectionKey<Set<() => boolean>> = Symbol('dialogPendingEdits')
