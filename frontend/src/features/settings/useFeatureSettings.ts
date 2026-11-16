import { computed, onMounted, ref, watch } from 'vue'
import { settingsApi, type SystemSettings } from '@/api/settings'
import { errorMessage } from '@/api/client'
import { useSession } from '@/stores/session'
import { useLifetime } from '@/features/shared/useLifetime'
import { useRequest } from '@/features/shared/useRequest'

export function useFeatureSettings(group: 'oauth' | 'api_keys') {
  const request = useRequest(settingsApi.get)
  const settings = ref<SystemSettings>()
  const saved = ref('')
  const saving = ref(false)
  const error = ref('')
  const session = useSession()
  const signal = useLifetime()
  let pending: string | undefined
  let running: Promise<void> | undefined
  const snapshot = () => JSON.stringify(settings.value?.[group])
  const dirty = computed(() => !!settings.value && snapshot() !== saved.value)
  const available = computed(
    () => !!settings.value?.[group].enabled && !!saved.value && !!JSON.parse(saved.value).enabled,
  )
  watch(request.data, (value) => {
    if (!value) return
    settings.value = structuredClone(value)
    saved.value = snapshot()
  })
  onMounted(request.refresh)

  async function drain() {
    saving.value = true
    try {
      while (pending !== undefined && !signal.aborted) {
        const submitted = pending
        pending = undefined
        if (submitted === saved.value) {
          error.value = ''
          continue
        }
        error.value = ''
        try {
          const sent = JSON.parse(submitted)
          const result = await settingsApi.save(
            group === 'api_keys' ? 'api-keys' : 'oauth',
            { [group]: sent },
            signal,
          )
          if (signal.aborted) return
          const confirmed = result.settings[group]
          const current = settings.value![group] as unknown as Record<string, unknown>
          // 只确认本次提交的字段，保留请求期间继续输入的内容。
          for (const [field, value] of Object.entries(confirmed)) {
            if (current[field] === sent[field]) current[field] = value
          }
          saved.value = JSON.stringify(confirmed)
          if (session.context) session.context.features[group] = confirmed.enabled
        } catch (reason) {
          if (!signal.aborted) error.value = errorMessage(reason)
        }
      }
    } finally {
      saving.value = false
      running = undefined
    }
  }
  function save() {
    if (!settings.value || signal.aborted) return Promise.resolve()
    if (!dirty.value && !saving.value) {
      error.value = ''
      return Promise.resolve()
    }
    pending = snapshot()
    return (running ||= drain())
  }
  return {
    settings,
    loading: request.loading,
    loadError: request.error,
    refresh: request.refresh,
    saving,
    error,
    dirty,
    available,
    save,
  }
}
