import { onBeforeUnmount } from 'vue'

// 页面或弹窗卸载时结束请求，调用方在后续异步步骤前检查 aborted。
export function useLifetime() {
  const controller = new AbortController()
  onBeforeUnmount(() => controller.abort())
  return controller.signal
}
