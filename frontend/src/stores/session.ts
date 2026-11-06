import { defineStore } from 'pinia'
import { ref } from 'vue'
import { sessionApi, type Bootstrap } from '@/api/session'
import { errorMessage } from '@/api/client'

export const useSession = defineStore('session', () => {
  const context = ref<Bootstrap>()
  const error = ref('')
  let pending: Promise<void> | undefined
  async function refresh() {
    error.value = ''
    pending ||= sessionApi
      .bootstrap()
      .then((value) => {
        context.value = value
      })
      .catch((reason) => {
        error.value = errorMessage(reason)
        throw reason
      })
      .finally(() => {
        pending = undefined
      })
    await pending
  }
  function clear() {
    context.value = undefined
  }
  return { context, error, refresh, clear }
})
