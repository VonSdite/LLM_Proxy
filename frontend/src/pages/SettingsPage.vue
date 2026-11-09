<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { settingsApi, type SettingsGroup, type SystemSettings } from '@/api/settings'
import { errorMessage } from '@/api/client'
import { useSession } from '@/stores/session'
import { useRequest } from '@/features/shared/useRequest'
import { useLifetime } from '@/features/shared/useLifetime'
import ProxySettings from '@/features/shared/ProxySettings.vue'
import { isProxyUrl } from '@/features/shared/proxy'
import DesensitizationHelp from '@/features/shared/DesensitizationHelp.vue'
import SettingSection from '@/features/settings/SettingSection.vue'
import {
  UiAlert,
  UiButton,
  UiDialog,
  UiEmptyState,
  UiField,
  UiInput,
  UiNumberInput,
  UiSecretInput,
  UiSelect,
  UiSwitch,
  UiHelpPopover,
  UiPageHeader,
  notify,
} from '@/components/ui'
const request = useRequest(settingsApi.get)
const settings = ref<SystemSettings>()
const savedSettings = ref<SystemSettings>()
const reloadOpen = ref(false)
const saving = ref<SettingsGroup>()
const errors = reactive<Record<string, string>>({})
const saveErrors = reactive<Partial<Record<SettingsGroup, string>>>({})
const session = useSession()
const router = useRouter()
const signal = useLifetime()
watch(request.data, (value) => {
  if (value) {
    settings.value = structuredClone(value)
    savedSettings.value = structuredClone(value)
    Object.keys(errors).forEach((key) => delete errors[key])
    Object.keys(saveErrors).forEach((key) => delete saveErrors[key as SettingsGroup])
  }
})
onMounted(request.refresh)
const groupKeys = {
  basic: ['server', 'admin'],
  'client-ip': ['client_ip'],
  debug: ['logging'],
  oauth: ['oauth'],
  'api-keys': ['api_keys'],
  'model-mapping': ['model_mapping'],
} as const
function payload(settings: SystemSettings, group: SettingsGroup): Partial<SystemSettings> {
  return Object.fromEntries(groupKeys[group].map((key) => [key, settings[key]]))
}
function dirty(group: SettingsGroup) {
  return (
    !!settings.value &&
    !!savedSettings.value &&
    JSON.stringify(payload(settings.value, group)) !==
      JSON.stringify(payload(savedSettings.value, group))
  )
}
const hasChanges = computed(() => (Object.keys(groupKeys) as SettingsGroup[]).some(dirty))
function reload() {
  if (saving.value || request.loading.value) return
  if (hasChanges.value) reloadOpen.value = true
  else void request.refresh()
}
async function confirmReload() {
  await request.refresh()
  if (!signal.aborted && !request.error.value) reloadOpen.value = false
}
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
    if (!isProxyUrl(s.oauth.proxy)) errors.proxy = '请输入完整的 HTTP、HTTPS 或 SOCKS5 代理地址'
  }
  return !Object.keys(errors).length
}
async function save(group: SettingsGroup) {
  if (saving.value || request.loading.value || !settings.value || !dirty(group) || !validate(group))
    return
  saving.value = group
  saveErrors[group] = ''
  const submitted = JSON.parse(
    JSON.stringify(payload(settings.value, group)),
  ) as Partial<SystemSettings>
  try {
    const result = await settingsApi.save(group, submitted, signal)
    if (signal.aborted) return
    // 服务端规范化当前分组，其余分组保留各自的未保存草稿。
    const confirmed = payload(result.settings, group)
    Object.assign(settings.value, confirmed)
    Object.assign(savedSettings.value!, JSON.parse(JSON.stringify(confirmed)))
    notify(result.server_restart_required ? '已保存，监听设置在服务重启后生效' : '已保存')
    if (result.auth_config_changed) {
      session.clear()
      await session.refresh()
      if (result.settings.auth_enabled && !signal.aborted) await router.replace('/login')
    } else await session.refresh()
  } catch (reason) {
    if (!signal.aborted) saveErrors[group] = errorMessage(reason)
  } finally {
    saving.value = undefined
  }
}
</script>
<template>
  <div class="page settings-page">
    <UiPageHeader title="系统设置">
      <UiButton
        icon="refresh"
        :loading="request.loading.value"
        :disabled="!!saving"
        @click="reload"
      >
        重新读取
      </UiButton>
    </UiPageHeader>
    <UiAlert v-if="request.error.value">{{ request.error.value }}</UiAlert>
    <UiEmptyState v-if="!settings" :loading="request.loading.value" />
    <template v-else>
      <div class="settings-panel" :inert="request.loading.value || undefined">
        <SettingSection
          id="basic"
          class="full-width"
          title="基础设置"
          :saving="saving === 'basic'"
          :dirty="dirty('basic')"
          :disabled="!!saving"
          :error="saveErrors.basic"
          @save="save('basic')"
        >
          <div class="form-grid form-grid--four">
            <UiField
              label="监听地址"
              :error="errors.host"
              help="0.0.0.0 监听所有 IPv4 接口；127.0.0.1 仅允许本机访问。修改后需要重启服务。"
            >
              <UiInput v-model="settings.server.host" />
            </UiField>
            <UiField
              label="端口"
              :error="errors.port"
              help="服务监听的 TCP 端口，范围 1–65535。保存后需重启服务才生效，客户端 API 地址和反向代理转发端口也应对应调整。"
            >
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
              help="账号和密码均非空时，后台页面与管理 API 需要登录；任意一项为空即关闭登录校验。保存认证配置的变化后，现有登录会话失效。"
            >
              <UiInput v-model="settings.admin.username" autocomplete="off" />
            </UiField>
            <UiField
              label="管理密码"
              help="这是后台登录密码，与上游 API Key 无关。清空并保存会关闭后台登录校验，不表示保留旧密码。"
            >
              <UiSecretInput v-model="settings.admin.password" />
            </UiField>
          </div>
        </SettingSection>
        <SettingSection
          id="access"
          class="full-width"
          title="客户端 IP"
          :saving="saving === 'client-ip'"
          :dirty="dirty('client-ip')"
          :disabled="!!saving"
          :error="saveErrors['client-ip']"
          @save="save('client-ip')"
        >
          <div class="form-grid settings-access-grid">
            <div class="settings-switch-row">
              <div class="actions">
                <span>使用代理转发的 IP</span>
                <UiHelpPopover label="使用代理转发的 IP 说明">
                  <p>
                    关闭时，白名单、用户模型权限、日志和统计使用连接到本服务的对端
                    IP。启用后使用指定请求头中的客户端 IP。
                  </p>
                  <p>
                    适用于 Nginx
                    等可信反向代理；代理应覆盖该请求头。请求头缺失或地址无效时回退到对端 IP。
                  </p>
                </UiHelpPopover>
              </div>
              <UiSwitch v-model="settings.client_ip.real_ip_enabled" label="使用代理转发的 IP" />
            </div>
            <UiField
              label="IP 请求头"
              :error="errors.header"
              help="填写反向代理传递真实客户端 IP 的请求头名称，例如 X-Real-IP 或 X-Forwarded-For。逗号分隔多个地址时取第一个；只有启用代理转发 IP 后才读取。"
            >
              <UiInput
                v-model="settings.client_ip.real_ip_header"
                :disabled="!settings.client_ip.real_ip_enabled"
                placeholder="X-Real-IP"
              />
            </UiField>
          </div>
        </SettingSection>
        <SettingSection
          title="API Key"
          :saving="saving === 'api-keys'"
          :dirty="dirty('api-keys')"
          :disabled="!!saving"
          :error="saveErrors['api-keys']"
          @save="save('api-keys')"
        >
          <div class="settings-switch-row">
            <div class="actions">
              <span>启用 API Key 管理</span>
              <UiHelpPopover label="启用 API Key 管理说明">
                <p>
                  启用后显示 API Key 管理页面，下游模型请求需携带已启用的 Key，支持 Authorization:
                  Bearer 密钥 或 X-API-Key。
                </p>
                <p>
                  每个 Key 独立限制模型权限和累计 Token 配额。同时开启用户白名单时，还需通过 IP
                  校验，模型权限取两者交集。
                </p>
                <p>
                  关闭后保留 Key 记录，但模型接口不再要求此项认证；后台登录仍由管理账号与密码控制。
                </p>
              </UiHelpPopover>
            </div>
            <UiSwitch v-model="settings.api_keys.enabled" label="启用 API Key 管理" />
          </div>
        </SettingSection>
        <SettingSection
          title="模型映射"
          :saving="saving === 'model-mapping'"
          :dirty="dirty('model-mapping')"
          :disabled="!!saving"
          :error="saveErrors['model-mapping']"
          @save="save('model-mapping')"
        >
          <div class="settings-switch-row">
            <div class="actions">
              <span>启用模型映射</span>
              <UiHelpPopover label="启用模型映射说明">
                <p>
                  为客户端提供固定模型 ID，再按优先级或粘滞策略转发到实际目标。例如客户端一直使用
                  my-model，上游变化时只调整映射。
                </p>
                <p>
                  启用后显示模型映射页面并发布已启用的映射
                  ID。关闭后保留映射记录，但停止映射路由；原 Provider / OAuth
                  模型按其自身设置提供服务。
                </p>
              </UiHelpPopover>
            </div>
            <UiSwitch v-model="settings.model_mapping.enabled" label="启用模型映射" />
          </div>
        </SettingSection>
        <SettingSection
          id="oauth"
          class="full-width"
          title="OAuth 网络"
          :saving="saving === 'oauth'"
          :dirty="dirty('oauth')"
          :disabled="!!saving"
          :error="saveErrors.oauth"
          @save="save('oauth')"
        >
          <div class="form-grid">
            <div class="settings-switch-row">
              <div class="actions">
                <span>启用 OAuth</span>
                <UiHelpPopover label="启用 OAuth 说明">
                  启用后显示 OAuth 管理页面，并允许使用已配置账号的 OAuth 模型。关闭后停止 OAuth
                  模型入口，已有认证文件保留。
                </UiHelpPopover>
              </div>
              <UiSwitch v-model="settings.oauth.enabled" label="启用 OAuth" />
            </div>
            <div class="form-grid full-width">
              <ProxySettings
                v-model:mode="settings.oauth.proxy_mode"
                v-model:url="settings.oauth.proxy"
                :class="{ 'full-width': settings.oauth.proxy_mode === 'custom' }"
                purpose="oauth"
                :error="errors.proxy"
                :disabled="saving === 'oauth'"
              />
            </div>
            <div class="settings-switch-row">
              <div class="actions">
                <span>验证 TLS 证书</span>
                <UiHelpPopover label="验证 TLS 证书说明">
                  控制 OAuth 登录、Token 刷新、额度查询和上游模型请求的 HTTPS 证书校验。关闭时仍使用
                  HTTPS，但不验证对端证书；自签名或抓包代理可据此配置。此项不控制本服务的入站
                  HTTPS。
                </UiHelpPopover>
              </div>
              <UiSwitch v-model="settings.oauth.verify_ssl" label="验证 TLS 证书" />
            </div>
            <div class="settings-switch-row">
              <div class="actions">
                <span>安全脱敏</span>
                <UiHelpPopover label="OAuth 安全脱敏说明">
                  <DesensitizationHelp scope="oauth" />
                </UiHelpPopover>
              </div>
              <UiSwitch v-model="settings.oauth.safe_desensitization_enabled" label="安全脱敏" />
            </div>
          </div>
        </SettingSection>
        <SettingSection
          id="debug"
          class="full-width"
          title="日志与调试"
          :saving="saving === 'debug'"
          :dirty="dirty('debug')"
          :disabled="!!saving"
          :error="saveErrors.debug"
          @save="save('debug')"
        >
          <div class="stack">
            <div class="form-grid">
              <UiField
                label="日志目录"
                :error="errors.logPath"
                help="运行 LLM Proxy 的机器上保存 app.log、access.log 和 llm_request_trace.log 的目录。相对路径以服务工作目录为基准；保存后重新装配日志输出，无需重启，已有日志不会自动搬迁。"
              >
                <UiInput v-model="settings.logging.path" mono />
              </UiField>
              <UiField
                label="日志级别"
                help="控制应用日志的详细程度：DEBUG 最详细，INFO 记录常规运行，WARNING 只记录警告及更严重问题，ERROR / CRITICAL 更精简。此项与下方 LLM 请求调试日志开关独立。"
              >
                <UiSelect v-model="settings.logging.level" :options="levels" />
              </UiField>
            </div>
            <div class="settings-switch-row">
              <div class="actions">
                <span>LLM 请求调试日志</span>
                <UiHelpPopover label="LLM 请求调试日志说明">
                  <p>
                    开启后，将下游请求、转换后的上游请求、上游响应和转换后的下游响应四个阶段写入日志目录的
                    llm_request_trace.log，包括请求行、Header 和 Payload。
                  </p>
                  <p>
                    用于检查协议转换与转发内容，可能包含完整提示词和凭据，日志体积也会明显增加；关闭后停止记录新的
                    Trace，已有文件保留。
                  </p>
                </UiHelpPopover>
              </div>
              <UiSwitch
                v-model="settings.logging.llm_request_debug_enabled"
                label="LLM 请求调试日志"
              />
            </div>
          </div>
        </SettingSection>
      </div>
    </template>
    <UiDialog
      v-model:open="reloadOpen"
      title="重新读取配置？"
      size="sm"
      :busy="request.loading.value"
    >
      <p>当前有未保存的修改，重新读取会丢弃这些修改。</p>
      <UiAlert v-if="request.error.value">{{ request.error.value }}</UiAlert>
      <template #footer>
        <UiButton :disabled="request.loading.value" @click="reloadOpen = false">继续编辑</UiButton>
        <UiButton variant="danger" :loading="request.loading.value" @click="confirmReload">
          丢弃并重新读取
        </UiButton>
      </template>
    </UiDialog>
  </div>
</template>
<style scoped>
.settings-panel {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
  min-width: 0;
}

.settings-access-grid {
  align-items: end;
}

.settings-access-grid > .settings-switch-row {
  min-height: var(--control);
}

.settings-switch-row {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  min-height: var(--control-sm);
  font-size: var(--text-base);
}

@media (width <= 700px) {
  .settings-panel {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
