<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useSession } from '@/stores/session'
import { useFeatureSettings } from './useFeatureSettings'
import ProxySettings from '@/features/shared/ProxySettings.vue'
import DesensitizationHelp from '@/features/shared/DesensitizationHelp.vue'
import { isProxyUrl } from '@/features/shared/proxy'
import {
  UiAlert,
  UiButton,
  UiDialog,
  UiHelpPopover,
  UiIconButton,
  UiSwitch,
  UiTooltip,
} from '@/components/ui'

const props = defineProps<{ group: 'oauth' | 'api_keys' }>()
const emit = defineEmits<{ availability: [value: boolean] }>()
const { settings, loading, loadError, refresh, saving, error, dirty, available, save } =
  useFeatureSettings(props.group)
const session = useSession()
watch(available, (value) => emit('availability', value), { immediate: true })
const networkOpen = ref(false)
const proxyError = ref('')
const enabled = computed(
  () => settings.value?.[props.group].enabled ?? !!session.context?.features[props.group],
)
async function commit() {
  const oauth = settings.value?.oauth
  proxyError.value =
    props.group === 'oauth' &&
    oauth?.proxy_mode === 'custom' &&
    oauth.proxy.trim() &&
    !isProxyUrl(oauth.proxy)
      ? '请输入完整的 HTTP、HTTPS 或 SOCKS5 代理地址'
      : ''
  if (!proxyError.value) await save()
}
function toggle(value: boolean) {
  if (!settings.value) return
  settings.value[props.group].enabled = value
  void commit()
}
async function closeNetwork() {
  await commit()
  return !dirty.value && !proxyError.value
}
</script>
<template>
  <div class="feature-settings">
    <UiSwitch
      :model-value="enabled"
      :disabled="loading || !settings"
      :label="group === 'oauth' ? '启用 OAuth' : '启用 API Key'"
      @update:model-value="toggle"
    >
      {{ group === 'oauth' ? '启用 OAuth' : '启用 API Key' }}
    </UiSwitch>
    <UiHelpPopover :label="group === 'oauth' ? 'OAuth 开关说明' : 'API Key 开关说明'">
      <ul v-if="group === 'api_keys'">
        <li>启用后，模型请求必须携带已启用的 API Key，支持 Authorization: Bearer 或 X-API-Key。</li>
        <li>
          每个 Key 可设置模型权限、Token 配额及请求限速；同时开启用户白名单时，还需通过 IP
          校验，模型权限取两者交集。
        </li>
        <li>
          关闭后不再要求 API Key 认证，已有 Key
          与权限配置保留；重新启用后可操作下方列表，后台登录独立控制。
        </li>
      </ul>
      <ul v-else>
        <li>启用后，OAuth 账号及其模型可参与请求路由。</li>
        <li>
          关闭后停止 OAuth 模型入口，已有账号、认证文件和网络设置保留；重新启用后可操作下方列表。
        </li>
      </ul>
    </UiHelpPopover>
    <UiTooltip v-if="group === 'oauth'" text="网络设置">
      <UiIconButton
        :disabled="loading || !settings"
        icon="settings"
        label="网络设置"
        :title="undefined"
        aria-haspopup="dialog"
        @click="networkOpen = true"
      />
    </UiTooltip>
    <UiAlert v-if="loadError || ((error || proxyError) && !networkOpen)">
      {{ loadError || `未保存：${error || proxyError}` }}
      <UiButton size="sm" :loading="loading || saving" @click="loadError ? refresh() : commit()">
        重试
      </UiButton>
    </UiAlert>
    <UiDialog
      v-if="settings && group === 'oauth'"
      v-model:open="networkOpen"
      title="OAuth 网络设置"
      :before-close="closeNetwork"
      @update:open="!$event && commit()"
    >
      <form class="stack" @submit.prevent="commit">
        <ProxySettings
          v-model:mode="settings.oauth.proxy_mode"
          v-model:url="settings.oauth.proxy"
          purpose="oauth"
          :error="proxyError"
          @commit="commit"
        />
        <div class="form-grid">
          <div class="network-switch">
            <UiSwitch
              v-model="settings.oauth.verify_ssl"
              label="验证 TLS 证书"
              @update:model-value="commit"
            >
              验证 TLS 证书
            </UiSwitch>
            <UiHelpPopover label="验证 TLS 证书说明">
              <ul>
                <li>控制 OAuth 登录、Token 刷新、额度查询及模型请求的 HTTPS 证书校验。</li>
                <li>
                  关闭后仍使用
                  HTTPS，但不验证对端证书，适用于自签名证书或抓包代理；不影响本服务的入站 HTTPS。
                </li>
              </ul>
            </UiHelpPopover>
          </div>
          <div class="network-switch">
            <UiSwitch
              v-model="settings.oauth.safe_desensitization_enabled"
              label="安全脱敏"
              @update:model-value="commit"
            >
              安全脱敏
            </UiSwitch>
            <UiHelpPopover label="OAuth 安全脱敏说明">
              <DesensitizationHelp scope="oauth" />
            </UiHelpPopover>
          </div>
        </div>
        <UiAlert v-if="error">
          未保存：{{ error }}
          <UiButton size="sm" :loading="saving" @click="commit">重试</UiButton>
        </UiAlert>
      </form>
    </UiDialog>
  </div>
</template>
<style scoped>
.feature-settings,
.network-switch {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}
</style>
