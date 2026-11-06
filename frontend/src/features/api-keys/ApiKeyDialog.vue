<script setup lang="ts">
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
})
const busy = ref(false)
const error = ref('')
const quotaError = ref('')
const quotaValid = ref(true)
const signal = useLifetime()
async function submit() {
  quotaError.value =
    !quotaValid.value ||
    (draft.token_limit_k !== null &&
      (!Number.isInteger(draft.token_limit_k) || draft.token_limit_k < 1))
      ? '请输入大于 0 的整数，留空表示无限额'
      : ''
  if (busy.value || quotaError.value) return
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
</script>
<template>
  <UiDialog
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
          help="1k = 1000 Tokens。留空表示不限额；配额按此 Key 累计使用量计算。"
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
      <UiSwitch v-model="draft.enabled" :disabled="busy">启用</UiSwitch>
      <UiField label="模型权限" group>
        <ModelPermissionPicker
          v-model="draft.model_permissions"
          :models="models"
          :disabled="busy"
        />
      </UiField>
    </form>
    <template #footer>
      <UiButton :disabled="busy" @click="emit('close')">取消</UiButton>
      <UiButton variant="primary" type="submit" form="api-key-form" :loading="busy">保存</UiButton>
    </template>
  </UiDialog>
</template>
