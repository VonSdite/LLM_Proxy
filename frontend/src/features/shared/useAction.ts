import { ref } from 'vue'
import { errorMessage, isAbort } from '@/api/client'
import { notify } from '@/components/ui'
import { useLifetime } from './useLifetime'

export function useAction(after?: () => void | Promise<void>) {
  const busy = ref(false)
  const error = ref('')
  const signal = useLifetime()
  async function run(action: (signal: AbortSignal) => Promise<unknown>, message?: string) {
    if (busy.value || signal.aborted) return false
    busy.value = true
    error.value = ''
    try {
      await action(signal)
      if (signal.aborted) return false
      if (message) notify(message)
      await after?.()
      return !signal.aborted
    } catch (reason) {
      if (!signal.aborted && !isAbort(reason)) error.value = errorMessage(reason)
      return false
    } finally {
      busy.value = false
    }
  }
  return { busy, error, run }
}
