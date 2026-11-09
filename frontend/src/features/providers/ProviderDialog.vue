<script setup lang="ts">
import { useDraftChanges } from '@/features/shared/useDraftChanges'
import { computed, ref } from 'vue'
import { providersApi, type AuthGroup, type Provider } from '@/api/providers'
import { errorMessage } from '@/api/client'
import { useLifetime } from '@/features/shared/useLifetime'
import ProxySettings from '@/features/shared/ProxySettings.vue'
import DesensitizationHelp from '@/features/shared/DesensitizationHelp.vue'
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
const modelsDirty = ref(false)
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
const dirty = useDraftChanges(() => ({ draft: draft.value, authMode: authMode.value }))
</script>
<template>
  <UiDialog
    :save="submit"
    :dirty="dirty || modelsDirty"
    open
    :title="provider ? '编辑 Provider' : '新增 Provider'"
    size="xl"
    :busy="busy"
    @update:open="emit('close')"
  >
    <form id="provider-form" class="stack" @submit.prevent="submit">
      <UiAlert v-if="error">{{ error }}</UiAlert>
      <div class="form-grid provider-fields">
        <UiField
          label="Provider 名称"
          required
          :error="errors.name"
          :help="[
            '作用：作为下游模型 ID 的前缀。',
            '示例：名称 main、上游模型 gpt-example，对外 ID 为 main/gpt-example。',
          ]"
        >
          <UiInput v-model="draft.name" maxlength="64" autofocus :disabled="busy" />
        </UiField>
        <UiField
          label="API 地址"
          required
          :error="errors.api"
          :help="[
            '填写实际处理请求的完整上游端点，路径需与所选上游格式匹配。',
            'Chat Completions：通常以 /v1/chat/completions 结尾。',
            'Responses：通常以 /v1/responses 结尾。',
            'Claude Messages：通常以 /v1/messages 结尾。',
          ]"
        >
          <UiInput
            v-model="draft.api"
            placeholder="https://api.example.com/v1/chat/completions"
            autocomplete="off"
            data-1p-ignore
            data-lpignore="true"
            :disabled="busy"
          />
        </UiField>
        <UiField label="上游格式">
          <template #help>
            <p>按上游接口支持的协议选择，不按模型名称选择。代理会转换客户端协议。</p>
            <ul>
              <li>
                <strong>OpenAI Chat Completions</strong>
                <br />
                常见路径：
                <code>/v1/chat/completions</code>
                <br />
                请求字段：
                <code>messages</code>
              </li>
              <li>
                <strong>OpenAI Responses</strong>
                <br />
                常见路径：
                <code>/v1/responses</code>
                <br />
                请求字段：
                <code>input</code>
                、
                <code>instructions</code>
              </li>
              <li>
                <strong>Claude Messages</strong>
                <br />
                常见路径：
                <code>/v1/messages</code>
                <br />
                请求字段：
                <code>messages</code>
              </li>
            </ul>
            <p>拉取模型仅获取模型列表，不会自动选择上游格式。</p>
          </template>
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
        <UiField
          label="认证方式"
          :help="[
            'API Key：使用单个上游密钥。',
            'Auth Group：从可复用的认证组中选择凭据，每个条目可设置并发上限、冷却时间和请求配额。',
          ]"
        >
          <UiSelect
            v-model="authMode"
            :options="[
              { value: 'key', label: 'API Key' },
              { value: 'group', label: 'Auth Group' },
            ]"
            :disabled="busy"
          />
        </UiField>
        <UiField
          v-if="authMode === 'key'"
          label="API Key"
          :help="[
            '填写上游服务提供的密钥。',
            '本项目为下游客户端创建的 API Key 不用于这里的上游认证。',
            '上游无需认证时可留空。',
          ]"
        >
          <UiSecretInput v-model="draft.api_key" placeholder="可留空" :disabled="busy" />
        </UiField>
        <UiField
          v-else
          label="Auth Group"
          required
          :error="errors.auth_group"
          :help="[
            '选择在 Auth Groups 中维护的认证组。',
            '正式请求：按组策略选择可用条目。',
            '拉取与测试：使用表单下方选定的测试 Auth Entry。',
          ]"
        >
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
        <UiField
          label="Hook"
          :help="[
            '路径：填写相对于项目 hooks/ 目录的 Python 文件路径，例如 example_hook.py；不能超出该目录。',
            '模块：需导出 Hook 类，可处理请求头、请求体和响应体。',
            '留空：不加载 Hook。',
          ]"
        >
          <UiInput v-model="draft.hook" placeholder="example_hook.py" mono :disabled="busy" />
        </UiField>
        <div class="stack">
          <ProxySettings
            :mode="draft.proxy_mode"
            :url="draft.proxy || ''"
            :error="errors.proxy"
            :disabled="busy"
            @update:mode="draft.proxy_mode = $event"
            @update:url="draft.proxy = $event"
          />
        </div>
        <UiField
          label="请求超时"
          unit="秒"
          :error="errors.timeout_seconds"
          :help="[
            '作用：限制上游连接与读取的等待时间，单位为秒。',
            '流式响应：主要限制等待下一段数据的时间，不是整段对话的总时长。',
            '留空：使用 1200 秒。',
          ]"
        >
          <UiNumberInput
            v-model="draft.timeout_seconds"
            :min="1"
            :step="1"
            placeholder="1200"
            :disabled="busy"
            @validity-change="validity.timeout_seconds = $event"
          />
        </UiField>
        <UiField
          label="最大尝试次数"
          :error="errors.max_retries"
          :help="[
            '计数：包含首次请求，填 1 表示只尝试一次。',
            '重试条件：连接异常，或 408、409、425、429、500、502、503、504 等可重试错误。',
            '影响：每次重试会消耗一次尝试机会，可能延长等待时间。',
            '留空：最多尝试 3 次。',
          ]"
        >
          <UiNumberInput
            v-model="draft.max_retries"
            :min="1"
            :step="1"
            placeholder="3"
            :disabled="busy"
            @validity-change="validity.max_retries = $event"
          />
        </UiField>
      </div>
      <div class="form-grid provider-fields">
        <UiField
          inline
          label="证书校验"
          :help="[
            '开启：校验上游 HTTPS 证书的签发机构与主机名。',
            '关闭：可连接自签名或抓包代理证书，但不再验证上游服务端身份。',
            '范围：仅作用于上游连接，不影响下游访问本服务的 HTTPS 设置。',
          ]"
        >
          <UiSwitch v-model="draft.verify_ssl" label="证书校验" :disabled="busy" />
        </UiField>
        <UiField
          inline
          label="强制上游流式"
          :help="[
            '适用场景：仅支持或更适合流式响应的上游。',
            '开启后：客户端未传 stream 或传 false 时，也以流式访问上游。',
            '非流式客户端：代理在内存中合并上游响应，再返回一个 JSON 响应。',
            '流式客户端：仍逐段返回响应。',
          ]"
        >
          <UiSwitch v-model="draft.force_upstream_stream" label="强制上游流式" :disabled="busy" />
        </UiField>
        <UiField inline label="安全脱敏">
          <template #help><DesensitizationHelp scope="provider" /></template>
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
        @dirty-change="modelsDirty = $event"
      />
    </form>
    <template #footer="{ close }">
      <UiButton :disabled="busy" @click="close()">取消</UiButton>
      <UiButton variant="primary" type="submit" form="provider-form" :loading="busy">保存</UiButton>
    </template>
  </UiDialog>
</template>
<style scoped>
.provider-fields {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  align-items: start;
}

@media (width <= 900px) {
  .provider-fields {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (width <= 600px) {
  .provider-fields {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
