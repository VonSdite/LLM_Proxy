<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { authGroupsApi, providersApi, type AuthEntry, type ModelTestResult } from '@/api/providers'
import { errorMessage, isAbort } from '@/api/client'
import ModelTestStatus from '@/features/providers/ModelTestResult.vue'
import { UiButton, UiSelect, UiAlert } from '@/components/ui'
const props = defineProps<{ model: string; disabled?: boolean }>()
const entries = ref<AuthEntry[]>([])
const authEntry = ref('')
const busy = ref(false)
const result = ref<ModelTestResult>()
const unsupported = ref('')
let controller: AbortController | undefined
let groupName = ''
function cancel() {
  controller?.abort()
  busy.value = false
  result.value = undefined
  unsupported.value = ''
}
watch(
  () => props.model,
  () => {
    cancel()
    entries.value = []
    authEntry.value = ''
    groupName = ''
  },
)
onBeforeUnmount(cancel)
async function test() {
  if (busy.value || props.disabled || !props.model) return
  controller?.abort()
  const current = new AbortController()
  controller = current
  const model = props.model
  busy.value = true
  result.value = undefined
  unsupported.value = ''
  try {
    const providers = await providersApi.list(current.signal)
    if (current.signal.aborted) return
    const provider = providers.find((item) =>
      item.model_list.some((value) => `${item.name}/${value}` === model),
    )
    if (!provider) {
      unsupported.value = '性能测试支持 Provider 目标；OAuth 目标可通过对应模型接口验证。'
      return
    }
    if (provider.auth_group) {
      const group = await authGroupsApi.get(provider.auth_group, current.signal)
      if (current.signal.aborted) return
      entries.value = group.entries
      if (groupName !== group.name || !entries.value.some((entry) => entry.id === authEntry.value))
        authEntry.value =
          group.entries.find((entry) => entry.enabled)?.id || group.entries[0]?.id || ''
      groupName = group.name
      if (!authEntry.value) throw new Error('Auth Group 中没有可测试的条目')
    } else {
      entries.value = []
      authEntry.value = ''
    }
    const response = await providersApi.test(
      {
        ...provider,
        api_key: provider.auth_group ? '' : provider.api_key,
        auth_entry_id: authEntry.value,
        models: [model.slice(provider.name.length + 1)],
      },
      current.signal,
    )
    if (!current.signal.aborted && props.model === model) result.value = response.results[0]
  } catch (reason) {
    if (!isAbort(reason) && !current.signal.aborted)
      result.value = {
        requested_model: model,
        available: false,
        first_token_latency_ms: null,
        tps: null,
        response_model: null,
        error: errorMessage(reason),
      }
  } finally {
    if (controller === current) busy.value = false
  }
}
</script>
<template>
  <div class="target-test">
    <div v-if="entries.length" class="target-test-entry">
      <UiSelect
        v-model="authEntry"
        :options="entries.map((entry) => ({ value: entry.id, label: entry.id }))"
        aria-label="测试 Auth Entry"
        :disabled="busy || disabled"
      />
    </div>
    <UiButton size="sm" :loading="busy" :disabled="disabled || !model" @click="test">测试</UiButton>
    <ModelTestStatus :result="result" />
    <UiAlert v-if="unsupported" variant="info">{{ unsupported }}</UiAlert>
  </div>
</template>
<style scoped>
.target-test {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.target-test-entry {
  width: min(220px, 100%);
}
</style>
