<script setup lang="ts">
import { useDraftChanges } from '@/features/shared/useDraftChanges'
import { reactive, ref } from 'vue'
import { apiKeysApi, type ApiKey, type ApiKeyInput } from '@/api/api-keys'
import { errorMessage } from '@/api/client'
import { useLifetime } from '@/features/shared/useLifetime'
import ModelPermissionPicker from '@/features/shared/ModelPermissionPicker.vue'
import {
  UiAlert,
  UiButton,
  UiDialog,
  UiField,
  UiInput,
  UiNumberInput,
  UiSwitch,
} from '@/components/ui'
const props = defineProps<{ apiKey?: ApiKey; models: string[] }>()
const emit = defineEmits<{ close: []; saved: [] }>()
const draft = reactive<ApiKeyInput>({
  name: props.apiKey?.name || '',
  enabled: props.apiKey?.enabled ?? true,
  model_permissions: props.apiKey?.model_permissions || '*',
  token_limit_k: props.apiKey?.token_limit_k ?? null,
  max_concurrency: props.apiKey?.max_concurrency ?? null,
  request_quota_per_minute: props.apiKey?.request_quota_per_minute ?? null,
  request_quota_per_day: props.apiKey?.request_quota_per_day ?? null,
})
const requestLimits = [
  {
    key: 'max_concurrency',
    label: '最大并发数',
    help: '此 Key 同时处理的客户端推理请求数，跨模型共享。流式请求结束、失败或断开后释放占用；达到上限时新请求返回 429，不排队。留空不限制。',
  },
  {
    key: 'request_quota_per_minute',
    label: '请求 / 分钟',
    help: '按服务本地时间的自然分钟计数，下一分钟重新计数。通过鉴权、模型权限等检查后接收的推理请求计一次，上游失败也计入，内部重试不重复计数；被拒绝的请求及查询模型列表不计入。达到上限返回 429，留空不限制。',
  },
  {
    key: 'request_quota_per_day',
    label: '请求 / 天',
    help: '按服务本地时间的自然日计数，次日零点重新计数。与每分钟限制使用相同口径，跨模型共享；计数持久保存，重启服务或修改限制不会清零。达到上限返回 429，留空不限制。',
  },
] as const
const limitErrors = reactive<Record<string, string>>({})
const limitValid = reactive<Record<string, boolean>>({})
const busy = ref(false)
const error = ref('')
const quotaError = ref('')
const quotaValid = ref(true)
const signal = useLifetime()
async function submit() {
  for (const { key } of requestLimits) {
    const value = draft[key]
    limitErrors[key] =
      limitValid[key] === false ||
      (value !== null && (!Number.isInteger(value) || value < 1 || value > 2147483647))
        ? '请输入 1–2147483647 的整数，留空表示不限'
        : ''
  }
  quotaError.value =
    !quotaValid.value ||
    (draft.token_limit_k !== null &&
      (!Number.isInteger(draft.token_limit_k) || draft.token_limit_k < 1))
      ? '请输入大于 0 的整数，留空表示无限额'
      : ''
  if (busy.value || quotaError.value || Object.values(limitErrors).some(Boolean)) return
  busy.value = true
  error.value = ''
  try {
    if (props.apiKey) await apiKeysApi.update(props.apiKey.id, draft, signal)
    else await apiKeysApi.create(draft, signal)
    if (signal.aborted) return
    emit('saved')
    emit('close')
  } catch (reason) {
    if (!signal.aborted) error.value = errorMessage(reason)
  } finally {
    busy.value = false
  }
}
const dirty = useDraftChanges(() => ({
  ...draft,
  model_permissions: Array.isArray(draft.model_permissions)
    ? [...draft.model_permissions].sort()
    : draft.model_permissions,
}))
</script>
<template>
  <UiDialog
    :save="submit"
    :dirty="dirty"
    open
    :title="apiKey ? '编辑 API Key' : '创建 API Key'"
    :busy="busy"
    @update:open="emit('close')"
  >
    <form id="api-key-form" class="stack" @submit.prevent="submit">
      <UiAlert v-if="error">{{ error }}</UiAlert>
      <div class="form-grid">
        <UiField label="名称">
          <UiInput v-model="draft.name" placeholder="API Key" autofocus :disabled="busy" />
        </UiField>
        <UiField
          label="Token 配额"
          unit="k"
          :help="[
            '1k = 1000 Tokens。限制此 Key 自创建以来累计记录的输入与输出总量，不会按日或按月重置；留空不限额。',
            '例如配额 100k、已用 30k，剩余为 70k。调整配额不会清空已用量；达到上限后拒绝新请求，已在进行的请求可能使总量超出上限。',
          ]"
          :error="quotaError"
        >
          <UiNumberInput
            v-model="draft.token_limit_k"
            :min="1"
            :step="1"
            placeholder="不限额"
            :disabled="busy"
            @validity-change="quotaValid = $event"
          />
        </UiField>
      </div>
      <div class="form-grid request-limits">
        <UiField
          v-for="field in requestLimits"
          :key="field.key"
          :label="field.label"
          :help="field.help"
          :error="limitErrors[field.key]"
        >
          <UiNumberInput
            v-model="draft[field.key]"
            :min="1"
            :max="2147483647"
            :step="1"
            placeholder="不限制"
            :disabled="busy"
            @validity-change="limitValid[field.key] = $event"
          />
        </UiField>
      </div>
      <UiSwitch v-model="draft.enabled" :disabled="busy">启用</UiSwitch>
      <UiField label="模型权限" group>
        <ModelPermissionPicker
          v-model="draft.model_permissions"
          :models="models"
          :disabled="busy"
        />
      </UiField>
    </form>
    <template #footer="{ close }">
      <UiButton :disabled="busy" @click="close()">取消</UiButton>
      <UiButton variant="primary" type="submit" form="api-key-form" :loading="busy">保存</UiButton>
    </template>
  </UiDialog>
</template>
<style scoped>
.request-limits {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

@media (width <= 600px) {
  .request-limits {
    grid-template-columns: 1fr;
  }
}
</style>
