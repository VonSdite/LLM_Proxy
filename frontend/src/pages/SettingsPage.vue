<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { settingsApi, type SettingsGroup, type SystemSettings } from '@/api/settings'
import { errorMessage } from '@/api/client'
import { useSession } from '@/stores/session'
import { useRequest } from '@/features/shared/useRequest'
import { useLifetime } from '@/features/shared/useLifetime'
import ProxySettings from '@/features/shared/ProxySettings.vue'
import SettingSection from '@/features/settings/SettingSection.vue'
import {
  UiAlert,
  UiButton,
  UiEmptyState,
  UiField,
  UiInput,
  UiNumberInput,
  UiSecretInput,
  UiSelect,
  UiSwitch,
  UiHelpPopover,
  notify,
} from '@/components/ui'
const request = useRequest(settingsApi.get)
const settings = ref<SystemSettings>()
const saving = ref<SettingsGroup>()
const errors = reactive<Record<string, string>>({})
const saveError = ref('')
const session = useSession()
const router = useRouter()
const signal = useLifetime()
watch(request.data, (value) => {
  if (value) settings.value = structuredClone(value)
})
onMounted(request.refresh)
const levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'].map((value) => ({
  value,
  label: value,
}))
function validate(group: SettingsGroup) {
  Object.keys(errors).forEach((key) => delete errors[key])
  const s = settings.value!
  if (group === 'basic') {
    if (!s.server.host.trim()) errors.host = '请输入监听地址'
    if (!Number.isInteger(s.server.port) || s.server.port < 1 || s.server.port > 65535)
      errors.port = '端口范围为 1–65535'
  }
  if (group === 'client-ip' && !/^[!#$%&'*+.^_`|~\w-]+$/.test(s.client_ip.real_ip_header))
    errors.header = '请输入合法的 HTTP 请求头名称'
  if (group === 'debug' && !s.logging.path.trim()) errors.logPath = '请输入日志目录'
  if (group === 'oauth' && s.oauth.proxy_mode === 'custom' && s.oauth.proxy.trim()) {
    try {
      if (!['http:', 'https:', 'socks5:', 'socks5h:'].includes(new URL(s.oauth.proxy).protocol))
        errors.proxy = '支持 HTTP、HTTPS、SOCKS5 和 SOCKS5H'
    } catch {
      errors.proxy = '请输入完整代理地址'
    }
  }
  return !Object.keys(errors).length
}
async function save(group: SettingsGroup) {
  if (saving.value || !settings.value || !validate(group)) return
  saving.value = group
  saveError.value = ''
  const s = settings.value
  const payloads: Record<SettingsGroup, Partial<SystemSettings>> = {
    basic: { server: s.server, admin: s.admin },
    'client-ip': { client_ip: s.client_ip },
    debug: { logging: s.logging },
    oauth: { oauth: s.oauth },
    'api-keys': { api_keys: s.api_keys },
    'model-mapping': { model_mapping: s.model_mapping },
  }
  try {
    const result = await settingsApi.save(group, payloads[group], signal)
    if (signal.aborted) return
    notify(result.server_restart_required ? '已保存，监听设置在服务重启后生效' : '已保存')
    if (result.auth_config_changed) {
      session.clear()
      await session.refresh()
      if (result.settings.auth_enabled && !signal.aborted) await router.replace('/login')
    } else await session.refresh()
  } catch (reason) {
    if (!signal.aborted) saveError.value = errorMessage(reason)
  } finally {
    saving.value = undefined
  }
}
</script>
<template>
  <div class="page settings-page">
    <header class="page-heading">
      <h1>系统设置</h1>
      <UiButton icon="refresh" :loading="request.loading.value" @click="request.refresh">
        重新读取
      </UiButton>
    </header>
    <UiAlert v-if="request.error.value">{{ request.error.value }}</UiAlert>
    <UiAlert v-if="saveError">{{ saveError }}</UiAlert>
    <UiEmptyState v-if="!settings" :loading="request.loading.value" />
    <div v-else class="settings-layout">
      <nav class="settings-nav" aria-label="设置分组">
        <a href="#basic">基础设置</a>
        <a href="#access">访问控制</a>
        <a href="#oauth">OAuth 网络</a>
        <a href="#debug">日志与调试</a>
      </nav>
      <div class="stack">
        <SettingSection
          id="basic"
          title="基础设置"
          :saving="saving === 'basic'"
          @save="save('basic')"
        >
          <div class="form-grid">
            <UiField
              label="监听地址"
              :error="errors.host"
              help="0.0.0.0 监听所有 IPv4 接口；127.0.0.1 仅允许本机访问。修改后需要重启服务。"
            >
              <UiInput v-model="settings.server.host" />
            </UiField>
            <UiField label="端口" :error="errors.port">
              <UiNumberInput
                :model-value="settings.server.port"
                :min="1"
                :max="65535"
                :step="1"
                required
                @commit="settings.server.port = $event ?? 0"
              />
            </UiField>
            <UiField
              label="管理账号"
              help="账号和密码均非空时启用后台认证。认证配置发生变化后，现有登录会话失效。"
            >
              <UiInput v-model="settings.admin.username" autocomplete="off" />
            </UiField>
            <UiField label="管理密码">
              <UiSecretInput v-model="settings.admin.password" />
            </UiField>
          </div>
        </SettingSection>
        <div id="access" class="stack">
          <SettingSection
            title="客户端 IP"
            :saving="saving === 'client-ip'"
            @save="save('client-ip')"
          >
            <div class="stack">
              <div class="settings-switch-row">
                <div class="actions">
                  <span>使用代理转发的 IP</span>
                  <UiHelpPopover>
                    仅在受信任的反向代理后启用；代理应覆盖该请求头，避免客户端伪造地址。
                  </UiHelpPopover>
                </div>
                <UiSwitch v-model="settings.client_ip.real_ip_enabled" label="使用代理转发的 IP" />
              </div>
              <UiField label="IP 请求头" :error="errors.header">
                <UiInput v-model="settings.client_ip.real_ip_header" placeholder="X-Real-IP" />
              </UiField>
            </div>
          </SettingSection>
          <SettingSection title="API Key" :saving="saving === 'api-keys'" @save="save('api-keys')">
            <div class="settings-switch-row">
              <div class="actions">
                <span>启用 API Key 管理</span>
                <UiHelpPopover>
                  启用后，代理请求需要携带有效的 API Key，并按 Key 的模型权限和 Token 配额校验。
                </UiHelpPopover>
              </div>
              <UiSwitch v-model="settings.api_keys.enabled" label="启用 API Key 管理" />
            </div>
          </SettingSection>
          <SettingSection
            title="模型映射"
            :saving="saving === 'model-mapping'"
            @save="save('model-mapping')"
          >
            <div class="settings-switch-row">
              <div class="actions">
                <span>启用模型映射</span>
                <UiHelpPopover>对外提供统一模型 ID，并按配置的策略路由到实际模型。</UiHelpPopover>
              </div>
              <UiSwitch v-model="settings.model_mapping.enabled" label="启用模型映射" />
            </div>
          </SettingSection>
        </div>
        <SettingSection
          id="oauth"
          title="OAuth 网络"
          :saving="saving === 'oauth'"
          @save="save('oauth')"
        >
          <div class="stack">
            <div class="settings-switch-row">
              <span>启用 OAuth</span>
              <UiSwitch v-model="settings.oauth.enabled" label="启用 OAuth" />
            </div>
            <div class="form-grid">
              <ProxySettings
                v-model:mode="settings.oauth.proxy_mode"
                v-model:url="settings.oauth.proxy"
                :error="errors.proxy"
              />
            </div>
            <div class="settings-switch-row">
              <span>验证 TLS 证书</span>
              <UiSwitch v-model="settings.oauth.verify_ssl" label="验证 TLS 证书" />
            </div>
            <div class="settings-switch-row">
              <div class="actions">
                <span>安全脱敏</span>
                <UiHelpPopover>
                  按现有规则处理出站请求中的敏感内容，并在响应中恢复对应内容。
                </UiHelpPopover>
              </div>
              <UiSwitch v-model="settings.oauth.safe_desensitization_enabled" label="安全脱敏" />
            </div>
          </div>
        </SettingSection>
        <SettingSection
          id="debug"
          title="日志与调试"
          :saving="saving === 'debug'"
          @save="save('debug')"
        >
          <div class="stack">
            <div class="form-grid">
              <UiField label="日志目录" :error="errors.logPath">
                <UiInput v-model="settings.logging.path" mono />
              </UiField>
              <UiField label="日志级别">
                <UiSelect v-model="settings.logging.level" :options="levels" />
              </UiField>
            </div>
            <div class="settings-switch-row">
              <div class="actions">
                <span>LLM 请求调试日志</span>
                <UiHelpPopover>记录模型请求与响应的调试信息，适用于排查转发问题。</UiHelpPopover>
              </div>
              <UiSwitch
                v-model="settings.logging.llm_request_debug_enabled"
                label="LLM 请求调试日志"
              />
            </div>
          </div>
        </SettingSection>
      </div>
    </div>
  </div>
</template>
<style scoped>
.settings-page {
  max-width: 1080px;
  margin-inline: auto;
}

.settings-layout {
  display: grid;
  grid-template-columns: 160px minmax(0, 1fr);
  gap: var(--space-6);
}

.settings-nav {
  display: grid;
  gap: var(--space-1);
  align-content: start;
  position: sticky;
  top: 92px;
  align-self: start;
}

.settings-nav a {
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-control);
  color: var(--muted);
  font-size: var(--text-sm);
}

.settings-nav a:hover {
  background: var(--hover);
  color: var(--text);
  text-decoration: none;
}

.settings-switch-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-4);
  min-height: var(--control-sm);
  font-size: var(--text-sm);
}

.stack > [id] {
  scroll-margin-top: 96px;
}

@media (width <= 800px) {
  .settings-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .settings-nav {
    position: static;
    display: flex;
    flex-wrap: wrap;
  }
}
</style>
