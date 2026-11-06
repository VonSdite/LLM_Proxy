<script setup lang="ts">
import { computed, ref } from 'vue'
import { providersApi, type AuthGroup, type Provider } from '@/api/providers'
import { errorMessage } from '@/api/client'
import { useLifetime } from '@/features/shared/useLifetime'
import ProxySettings from '@/features/shared/ProxySettings.vue'
import ProviderModels from './ProviderModels.vue'
import { providerDraft, providerErrors } from './provider'
import {
  UiAlert,
  UiButton,
  UiDialog,
  UiField,
  UiInput,
  UiNumberInput,
  UiSecretInput,
  UiSelect,
  UiSwitch,
} from '@/components/ui'
const props = defineProps<{ provider?: Provider; groups: AuthGroup[] }>()
const emit = defineEmits<{ close: []; saved: [] }>()
const draft = ref(providerDraft(props.provider))
const authMode = ref(props.provider?.auth_group ? 'group' : 'key')
const payload = computed<Provider>(() => ({
  ...draft.value,
  name: draft.value.name.trim(),
  api: draft.value.api.trim(),
  api_key: authMode.value === 'key' ? draft.value.api_key?.trim() || '' : '',
  auth_group: authMode.value === 'group' ? draft.value.auth_group || '' : '',
  proxy: draft.value.proxy_mode === 'custom' ? draft.value.proxy?.trim() || '' : '',
}))
const errors = ref<Record<string, string>>({})
const validity = ref<Record<string, boolean>>({})
const busy = ref(false)
const error = ref('')
const signal = useLifetime()
function modelsChanged(models: string[], hidden: string[]) {
  draft.value.model_list = models
  draft.value.hidden_model_list = hidden
}
async function submit() {
  errors.value = providerErrors(payload.value)
  if (authMode.value === 'group' && !draft.value.auth_group)
    errors.value.auth_group = '请选择 Auth Group'
  for (const [key, valid] of Object.entries(validity.value))
    if (!valid) errors.value[key] = '请输入正整数'
  if (busy.value || Object.keys(errors.value).length) return
  busy.value = true
  error.value = ''
  try {
    await providersApi.save(payload.value, props.provider?.name, signal)
    if (signal.aborted) return
    emit('saved')
    emit('close')
  } catch (reason) {
    if (signal.aborted) return
    error.value = errorMessage(reason)
    if (/name|exists/i.test(error.value)) errors.value.name = error.value
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <UiDialog
    open
    :title="provider ? '编辑 Provider' : '新增 Provider'"
    size="xl"
    :busy="busy"
    @update:open="emit('close')"
  >
    <form id="provider-form" class="stack" @submit.prevent="submit">
      <UiAlert v-if="error">{{ error }}</UiAlert>
      <div class="form-grid">
        <UiField label="Provider 名称" required :error="errors.name">
          <UiInput v-model="draft.name" maxlength="64" autofocus :disabled="busy" />
        </UiField>
        <UiField label="API 地址" required :error="errors.api">
          <UiInput
            v-model="draft.api"
            placeholder="https://api.example.com/v1/chat/completions"
            autocomplete="off"
            data-1p-ignore
            data-lpignore="true"
            :disabled="busy"
          />
        </UiField>
        <UiField label="上游格式" help="按上游接口支持的协议选择；代理会适配客户端使用的协议。">
          <UiSelect
            v-model="draft.source_format"
            :options="[
              { value: 'openai_chat', label: 'OpenAI Chat Completions' },
              { value: 'openai_responses', label: 'OpenAI Responses' },
              { value: 'claude_chat', label: 'Claude Messages' },
            ]"
            :disabled="busy"
          />
        </UiField>
        <UiField label="认证方式">
          <UiSelect
            v-model="authMode"
            :options="[
              { value: 'key', label: 'API Key' },
              { value: 'group', label: 'Auth Group' },
            ]"
            :disabled="busy"
          />
        </UiField>
        <UiField v-if="authMode === 'key'" label="API Key">
          <UiSecretInput v-model="draft.api_key" placeholder="可留空" :disabled="busy" />
        </UiField>
        <UiField v-else label="Auth Group" required :error="errors.auth_group">
          <UiSelect
            v-model="draft.auth_group"
            :options="
              groups.map((group) => ({
                value: group.name,
                label: group.name,
                description: `${group.entries.length} 个认证条目`,
              }))
            "
            :disabled="busy"
            placeholder="选择认证组"
          />
        </UiField>
        <UiField label="Hook" help="相对于项目 hooks/ 目录的 Python 文件路径，模块导出 Hook 类。">
          <UiInput v-model="draft.hook" placeholder="example_hook.py" mono :disabled="busy" />
        </UiField>
        <ProxySettings
          :mode="draft.proxy_mode"
          :url="draft.proxy || ''"
          :error="errors.proxy"
          :disabled="busy"
          @update:mode="draft.proxy_mode = $event"
          @update:url="draft.proxy = $event"
        />
      </div>
      <div class="form-grid form-grid--four">
        <UiField label="请求超时" unit="秒" :error="errors.timeout_seconds">
          <UiNumberInput
            v-model="draft.timeout_seconds"
            :min="1"
            :step="1"
            placeholder="1200"
            :disabled="busy"
            @validity-change="validity.timeout_seconds = $event"
          />
        </UiField>
        <UiField label="最大尝试次数" :error="errors.max_retries">
          <UiNumberInput
            v-model="draft.max_retries"
            :min="1"
            :step="1"
            placeholder="3"
            :disabled="busy"
            @validity-change="validity.max_retries = $event"
          />
        </UiField>
        <UiField label="证书校验">
          <UiSwitch v-model="draft.verify_ssl" label="证书校验" :disabled="busy" />
        </UiField>
        <UiField label="强制上游流式" help="上游始终使用流式请求，代理按客户端请求聚合或流式返回。">
          <UiSwitch v-model="draft.force_upstream_stream" label="强制上游流式" :disabled="busy" />
        </UiField>
        <UiField
          label="安全脱敏"
          help="请求中的常见凭据和个人信息替换为占位符，响应时恢复；规则可能误判或漏报。"
        >
          <UiSwitch
            v-model="draft.safe_desensitization_enabled"
            label="安全脱敏"
            :disabled="busy"
          />
        </UiField>
      </div>
      <UiAlert v-if="errors.models">{{ errors.models }}</UiAlert>
      <ProviderModels
        :provider="payload"
        :groups="groups"
        :disabled="busy"
        @change="modelsChanged"
      />
    </form>
    <template #footer>
      <UiButton :disabled="busy" @click="emit('close')">取消</UiButton>
      <UiButton variant="primary" type="submit" form="provider-form" :loading="busy">保存</UiButton>
    </template>
  </UiDialog>
</template>
