<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  UiButton,
  UiField,
  UiHelpPopover,
  UiInput,
  UiPopover,
  UiSecretInput,
  UiSelect,
} from '@/components/ui'
import { composeProxyUrl, splitProxyUrl } from './proxy'
const props = defineProps<{
  mode: string
  url: string
  error?: string
  disabled?: boolean
  modes?: { value: string; label: string }[]
  purpose?: 'provider' | 'oauth' | 'ssh'
}>()
const emit = defineEmits<{ 'update:mode': [value: string]; 'update:url': [value: string] }>()
const authenticationOpen = ref(false)
const fields = ref(splitProxyUrl(props.url))
let originalUrl = props.url
let originalFields = splitProxyUrl(originalUrl)
let emittedUrl: string | undefined
watch(
  () => props.url,
  (value) => {
    if (value === emittedUrl) return
    originalUrl = value
    originalFields = splitProxyUrl(value)
    Object.assign(fields.value, originalFields)
  },
)
function publish() {
  // 恢复原值时保留历史 URL 的写法，避免仅编码差异触发未保存提示。
  const unchanged =
    fields.value.address === originalFields.address &&
    fields.value.authenticated === originalFields.authenticated &&
    (!fields.value.authenticated ||
      (fields.value.username === originalFields.username &&
        fields.value.password === originalFields.password))
  emittedUrl = unchanged ? originalUrl : composeProxyUrl(fields.value)
  emit('update:url', emittedUrl)
}
function updateAddress(value: string) {
  const parsed = splitProxyUrl(value)
  if (parsed.authenticated) Object.assign(fields.value, parsed)
  else fields.value.address = value
  publish()
}
function updateCredentials() {
  fields.value.authenticated = !!(fields.value.username || fields.value.password)
  publish()
}
function clearAuthentication() {
  fields.value.username = ''
  fields.value.password = ''
  fields.value.authenticated = false
  publish()
}
const defaults = [
  { value: 'system', label: '系统代理' },
  { value: 'direct', label: '直连' },
  { value: 'custom', label: '自定义代理' },
]
</script>
<template>
  <UiField label="连接方式">
    <template #help>
      <p v-if="purpose === 'ssh'">
        控制运行 LLM Proxy 的机器如何连接 SSH 服务器，不会修改远程 Agent 的联网设置。
      </p>
      <p v-else-if="purpose === 'oauth'">
        控制服务端访问 OAuth 登录、Token 刷新、额度查询和模型接口的出站连接。
      </p>
      <p v-else>控制服务端访问当前 Provider 的上游 API、拉取模型和测试模型的出站连接。</p>
      <ul>
        <li>直连：不使用代理。</li>
        <li v-if="purpose === 'ssh'">
          系统代理：读取服务进程可见的系统代理，遵循代理绕过规则；没有匹配的代理时直连。
        </li>
        <li v-else>
          系统代理：读取服务进程的 HTTP_PROXY、HTTPS_PROXY、ALL_PROXY 等环境变量，并遵循
          NO_PROXY；没有配置时直连，不保证使用桌面系统代理。
        </li>
        <li>自定义代理：使用这里填写的代理地址。</li>
        <li>地址含义：127.0.0.1 指运行 LLM Proxy 的机器，不是打开网页的电脑。</li>
      </ul>
    </template>
    <div class="proxy-controls" :class="{ 'proxy-controls--custom': mode === 'custom' }">
      <UiSelect
        :model-value="mode"
        :compact="mode === 'custom'"
        :options="modes || defaults"
        :disabled="disabled"
        @update:model-value="emit('update:mode', $event)"
      />
      <UiField v-if="mode === 'custom'" label="代理地址" label-hidden :error="error">
        <div class="proxy-address-row">
          <UiInput
            :model-value="fields.address"
            :disabled="disabled"
            placeholder="socks5://127.0.0.1:1080"
            autocomplete="off"
            data-lpignore="true"
            data-1p-ignore="true"
            @update:model-value="updateAddress"
          />
          <UiPopover v-model:open="authenticationOpen" title="代理认证">
            <template #trigger>
              <UiButton
                :variant="fields.authenticated ? 'primary' : 'secondary'"
                :disabled="disabled"
                :aria-description="fields.authenticated ? '已设置账号密码' : '未设置账号密码'"
              >
                认证
              </UiButton>
            </template>
            <UiField label="代理用户名">
              <UiInput
                v-model="fields.username"
                :disabled="disabled"
                placeholder="用户名"
                autocomplete="off"
                autocapitalize="off"
                :spellcheck="false"
                data-lpignore="true"
                data-1p-ignore="true"
                @update:model-value="updateCredentials"
              />
            </UiField>
            <UiField label="代理密码">
              <UiSecretInput
                v-model="fields.password"
                :disabled="disabled"
                placeholder="密码"
                @update:model-value="updateCredentials"
              />
            </UiField>
            <template #footer="{ close }">
              <UiButton
                size="sm"
                :disabled="disabled || !fields.authenticated"
                @click="clearAuthentication"
              >
                清除
              </UiButton>
              <UiButton size="sm" variant="primary" @click="close()">完成</UiButton>
            </template>
          </UiPopover>
          <UiHelpPopover label="代理地址说明">
            <ul>
              <li>地址：填写协议、主机和端口，例如 socks5://127.0.0.1:1080。</li>
              <li>协议：支持 HTTP、HTTPS、SOCKS5、SOCKS5H。</li>
              <li>
                认证：点击按钮，在小窗中填写账号密码，随表单保存；「清除」表示不使用账号密码。
              </li>
              <li>特殊字符：直接填写原文，例如 @ 不用写成 %40，系统会自动编码。</li>
              <li>粘贴完整地址：自动拆分账号密码，已编码的字符会还原后显示。</li>
              <li>域名解析：SOCKS5 由运行 LLM Proxy 的机器解析，SOCKS5H 由代理解析。</li>
              <li v-if="purpose === 'ssh'">
                SSH 连接：HTTP / HTTPS 代理需允许 CONNECT 到 SSH 端口。
              </li>
              <li v-if="purpose === 'ssh'">
                凭据保存：SSH 密码与代理凭据仅在服务端当前会话内存中暂存，不写入机器列表。
              </li>
            </ul>
          </UiHelpPopover>
        </div>
      </UiField>
    </div>
  </UiField>
</template>
<style scoped>
.proxy-controls {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  align-items: start;
  gap: var(--space-2);
}

.proxy-controls--custom {
  grid-template-columns: 108px minmax(0, 1fr);
}

.proxy-address-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: var(--space-2);
}
</style>
