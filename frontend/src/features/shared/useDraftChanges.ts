import { computed } from 'vue'

/** 比较弹窗本次打开时的草稿，恢复原值后不再提示未保存。 */
export function useDraftChanges(read: () => unknown) {
  const baseline = JSON.stringify(read())
  return computed(() => JSON.stringify(read()) !== baseline)
}
