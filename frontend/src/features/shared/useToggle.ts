import { computed, reactive } from 'vue'
import { errorMessage, isAbort } from '@/api/client'
import { notify } from '@/components/ui'
import { useLifetime } from './useLifetime'

// 开关的临时显示与已确认数据分离，同一记录串行，不同记录可独立操作。
export function useToggle() {
  const changes = reactive(new Map<string, { value: boolean; group: string }>())
  const signal = useLifetime()
  const busy = computed(() => changes.size > 0)
  const pending = (key: string) => changes.has(key)
  const value = (key: string, saved: boolean) => changes.get(key)?.value ?? saved
  const blocked = (group: string) => [...changes.values()].some((change) => change.group === group)
  async function run<T>(
    key: string,
    next: boolean,
    save: (signal: AbortSignal) => Promise<T>,
    commit: (result: T) => void,
    group = key,
  ) {
    if (blocked(group) || signal.aborted) return false
    changes.set(key, { value: next, group })
    try {
      const result = await save(signal)
      if (signal.aborted) return false
      commit(result)
      return true
    } catch (reason) {
      if (!signal.aborted && !isAbort(reason)) notify(errorMessage(reason), 'danger')
      return false
    } finally {
      changes.delete(key)
    }
  }
  return { busy, pending, blocked, value, run }
}
