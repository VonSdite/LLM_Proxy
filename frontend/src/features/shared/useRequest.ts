import { onBeforeUnmount, ref, shallowRef } from 'vue'
import { errorMessage, isAbort } from '@/api/client'

// 请求序号将迟到响应与当前页面状态隔离，旧请求在新请求开始时取消。
export function useRequest<T>(load: (signal: AbortSignal) => Promise<T>) {
  const data = shallowRef<T>()
  const loading = ref(false)
  const error = ref('')
  let controller: AbortController | undefined
  let serial = 0
  let disposed = false
  let patches: ((value: T) => T)[] = []
  function patch(update: (value: T) => T) {
    if (disposed) return
    if (data.value !== undefined) data.value = update(data.value)
    if (loading.value) patches.push(update)
  }
  async function refresh() {
    if (disposed) return
    controller?.abort()
    controller = new AbortController()
    const current = ++serial
    patches = []
    loading.value = true
    error.value = ''
    try {
      const result = await load(controller.signal)
      // 已确认的局部写入优先于仍在途中的旧快照。
      if (current === serial)
        data.value = patches.reduce<T>((value, update) => update(value), result)
    } catch (reason) {
      if (current === serial && !isAbort(reason)) error.value = errorMessage(reason)
    } finally {
      if (current === serial) {
        loading.value = false
        patches = []
      }
    }
  }
  onBeforeUnmount(() => {
    disposed = true
    serial++
    controller?.abort()
  })
  return { data, loading, error, refresh, patch }
}
