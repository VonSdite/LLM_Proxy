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
  async function refresh() {
    if (disposed) return
    controller?.abort()
    controller = new AbortController()
    const current = ++serial
    loading.value = true
    error.value = ''
    try {
      const result = await load(controller.signal)
      if (current === serial) data.value = result
    } catch (reason) {
      if (current === serial && !isAbort(reason)) error.value = errorMessage(reason)
    } finally {
      if (current === serial) loading.value = false
    }
  }
  onBeforeUnmount(() => {
    disposed = true
    serial++
    controller?.abort()
  })
  return { data, loading, error, refresh }
}
