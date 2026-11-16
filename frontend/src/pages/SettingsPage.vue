<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { settingsApi, type SystemSettings } from '@/api/settings'
import { ApiError, errorMessage } from '@/api/client'
import { useRequest } from '@/features/shared/useRequest'
import { useLifetime } from '@/features/shared/useLifetime'
import SettingSection from '@/features/settings/SettingSection.vue'
import { isListenerHost } from '@/features/settings/listenerValidation'
import {
  UiAlert,
  UiButton,
  UiDialog,
  UiEmptyState,
  UiField,
  UiInput,
  UiIconButton,
  UiNumberInput,
  UiSecretInput,
  UiSelect,
  UiSwitch,
  UiPageHeader,
  notify,
} from '@/components/ui'
const request = useRequest(settingsApi.get)
const settings = ref<SystemSettings>()
const savedSettings = ref<SystemSettings>()
const reloadOpen = ref(false)
const changedLogin = ref<{ username: string; password: string; enabled: boolean }>()
const changedListener = ref<{ address: string; url: string | null; restart: boolean }>()
const portValid = ref(true)
type SectionGroup = 'basic' | 'admin' | 'client-ip' | 'debug'
const saving = reactive<Partial<Record<SectionGroup, boolean>>>({})
const busy = computed(() => Object.values(saving).some(Boolean))
const pending = new Map<SectionGroup, Partial<SystemSettings>>()
const errors = reactive<Record<string, string>>({})
const saveErrors = reactive<Partial<Record<SectionGroup, string>>>({})
const signal = useLifetime()
watch(request.data, (value) => {
  if (value) {
    settings.value = structuredClone(value)
    savedSettings.value = structuredClone(value)
    portValid.value = true
    Object.keys(errors).forEach((key) => delete errors[key])
    Object.keys(saveErrors).forEach((key) => delete saveErrors[key as SectionGroup])
  }
})
onMounted(request.refresh)
const groupKeys = {
  basic: ['server'],
  admin: ['admin'],
  'client-ip': ['client_ip'],
  debug: ['logging'],
} as const
function payload(settings: SystemSettings, group: SectionGroup): Partial<SystemSettings> {
  return Object.fromEntries(groupKeys[group].map((key) => [key, settings[key]]))
}
function dirty(group: SectionGroup) {
  return (
    !!settings.value &&
    !!savedSettings.value &&
    JSON.stringify(payload(settings.value, group)) !==
      JSON.stringify(payload(savedSettings.value, group))
  )
}
const hasChanges = computed(() => (Object.keys(groupKeys) as SectionGroup[]).some(dirty))
function reload() {
  if (busy.value || request.loading.value) return
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
function validate(group: SectionGroup) {
  const fields: Partial<Record<SectionGroup, string[]>> = {
    basic: ['host', 'port'],
    'client-ip': ['header'],
    debug: ['logPath'],
  }
  for (const key of fields[group] || []) delete errors[key]
  const s = settings.value!
  if (group === 'basic') {
    if (!s.server.host.trim()) errors.host = '请输入监听地址'
    else if (!isListenerHost(s.server.host)) errors.host = '请输入有效的 IPv4 或 IPv6 地址'
    if (
      !portValid.value ||
      !Number.isInteger(s.server.port) ||
      s.server.port < 1 ||
      s.server.port > 65535
    )
      errors.port = '端口范围为 1–65535'
  }
  if (group === 'client-ip' && !/^[!#$%&'*+.^_`|~\w-]+$/.test(s.client_ip.real_ip_header))
    errors.header = '请输入合法的 HTTP 请求头名称'
  if (group === 'debug' && !s.logging.path.trim()) errors.logPath = '请输入日志目录'
  return !(fields[group] || []).some((key) => errors[key])
}
async function save(group: SectionGroup) {
  if ((group === 'basic' || group === 'admin') && saving[group]) return
  if (group === 'basic' && settings.value)
    settings.value.server.host = settings.value.server.host.trim()
  if (
    group === 'client-ip' &&
    settings.value &&
    !settings.value.client_ip.real_ip_enabled &&
    !/^[!#$%&'*+.^_`|~\w-]+$/.test(settings.value.client_ip.real_ip_header)
  ) {
    settings.value.client_ip.real_ip_header = savedSettings.value!.client_ip.real_ip_header
  }
  if (
    changedLogin.value ||
    changedListener.value ||
    request.loading.value ||
    !settings.value ||
    !validate(group)
  )
    return
  if (!dirty(group) && !saving[group]) {
    saveErrors[group] = ''
    return
  }
  pending.set(group, JSON.parse(JSON.stringify(payload(settings.value, group))))
  if (saving[group]) return
  saving[group] = true
  try {
    while (pending.has(group) && !signal.aborted) {
      const submitted = pending.get(group)!
      pending.delete(group)
      saveErrors[group] = ''
      try {
        const result = await settingsApi.save(
          group === 'admin' ? 'basic' : group,
          submitted,
          signal,
        )
        if (signal.aborted) return
        // 仅合并仍与本次提交相同的字段，保留请求期间的新输入。
        for (const key of groupKeys[group]) {
          const current = settings.value[key] as Record<string, unknown>
          const sent = submitted[key] as Record<string, unknown>
          const confirmed = result.settings[key] as Record<string, unknown>
          for (const [field, value] of Object.entries(confirmed)) {
            if (current[field] === sent[field]) current[field] = value
          }
          Object.assign(savedSettings.value![key], confirmed)
        }
        if (group === 'basic') {
          if (result.server_listener_changed || result.server_restart_required) {
            const { host, port } = result.settings.server
            changedListener.value = {
              address: `${host.includes(':') ? `[${host}]` : host}:${port}`,
              url: result.server_access_url || null,
              restart: !!result.server_restart_required,
            }
            pending.delete(group)
          } else notify('已保存')
        }
        if (result.auth_config_changed) {
          // 预览服务端已保存的凭据，等待用户确认后再刷新登录状态。
          changedLogin.value = {
            ...result.settings.admin,
            enabled: result.settings.auth_enabled,
          }
          pending.delete(group)
          return
        }
      } catch (reason) {
        if (!signal.aborted) {
          if (group === 'basic') {
            if (reason instanceof ApiError && /^Server host /i.test(reason.message))
              errors.host = '请输入有效的 IPv4 或 IPv6 地址'
            else if (reason instanceof ApiError && /^Server port /i.test(reason.message))
              errors.port = '端口范围为 1–65535'
            else notify(`监听未保存：${errorMessage(reason)}`, 'danger')
          } else saveErrors[group] = errorMessage(reason)
        }
      }
    }
  } finally {
    saving[group] = false
  }
}
function continueAfterLoginChange() {
  window.location.replace(changedLogin.value?.enabled ? '/login' : window.location.href)
}
function continueAfterListenerChange() {
  if (changedListener.value?.url) window.location.assign(changedListener.value.url)
  else changedListener.value = undefined
}
function leaveGroup(event: FocusEvent, group: 'basic' | 'admin') {
  if (
    event.relatedTarget instanceof Node &&
    (event.currentTarget as HTMLElement).contains(event.relatedTarget)
  )
    return
  void save(group)
}
</script>
<template>
  <div class="page settings-page">
    <UiPageHeader title="系统设置">
      <UiIconButton
        icon="refresh"
        label="刷新设置"
        :loading="request.loading.value"
        :disabled="busy"
        @click="reload"
      />
    </UiPageHeader>
    <UiAlert v-if="request.error.value">{{ request.error.value }}</UiAlert>
    <UiEmptyState v-if="!settings" :loading="request.loading.value" />
    <template v-else>
      <div class="settings-panel" :inert="request.loading.value || undefined">
        <SettingSection
          id="basic"
          class="settings-half"
          title="服务监听"
          :saving="saving['basic']"
          @save="save('basic')"
        >
          <div
            class="form-grid"
            :inert="saving.basic || undefined"
            @focusout="leaveGroup($event, 'basic')"
            @keydown.enter.prevent="save('basic')"
          >
            <UiField
              label="监听地址"
              :error="errors.host"
              help="0.0.0.0 监听所有 IPv4 接口；127.0.0.1 仅允许本机访问。离开地址和端口编辑区域或按 Enter 自动保存并切换监听，已有请求继续完成。新地址无法绑定时保持原监听。"
            >
              <UiInput v-model="settings.server.host" @update:model-value="delete errors.host" />
            </UiField>
            <UiField
              label="端口"
              :error="errors.port"
              help="服务监听的 TCP 端口，范围 1–65535。内置服务保存后立即生效，无需重启 Python；客户端 API 地址、反向代理转发端口和容器端口映射需对应调整。外部 WSGI 宿主需自行调整监听。"
            >
              <UiNumberInput
                :model-value="settings.server.port"
                :min="1"
                :max="65535"
                :step="1"
                required
                @commit="settings.server.port = $event ?? 0"
                @validity-change="portValid = $event"
              />
            </UiField>
          </div>
        </SettingSection>
        <SettingSection
          title="管理登录"
          class="settings-half"
          :saving="saving.admin"
          :error="saveErrors.admin"
          @retry="save('admin')"
          @save="save('admin')"
        >
          <div
            class="form-grid"
            :inert="saving.admin || undefined"
            @focusout="leaveGroup($event, 'admin')"
            @keydown.enter.prevent="save('admin')"
          >
            <UiField
              label="管理账号"
              help="账号和密码均非空时，后台页面与管理 API 需要登录；任意一项为空即关闭登录校验。编辑完成后自动保存，现有登录会话失效。"
            >
              <UiInput v-model="settings.admin.username" autocomplete="off" />
            </UiField>
            <UiField
              label="管理密码"
              help="这是后台登录密码，与上游 API Key 无关。清空并结束编辑会关闭后台登录校验，不表示保留旧密码。"
            >
              <UiSecretInput v-model="settings.admin.password" />
            </UiField>
          </div>
        </SettingSection>
        <SettingSection
          id="access"
          class="settings-half"
          title="客户端 IP"
          :saving="saving['client-ip']"
          :error="saveErrors['client-ip']"
          @save="save('client-ip')"
          @retry="save('client-ip')"
        >
          <div class="form-grid settings-access-grid">
            <UiField inline class="settings-toggle" label="使用代理转发的 IP">
              <template #help>
                <p>
                  关闭时，白名单、用户模型权限、日志和统计使用连接到本服务的对端
                  IP。启用后使用指定请求头中的客户端 IP。
                </p>
                <p>
                  适用于 Nginx 等可信反向代理；代理应覆盖该请求头。请求头缺失或地址无效时回退到对端
                  IP。
                </p>
              </template>
              <UiSwitch
                v-model="settings.client_ip.real_ip_enabled"
                label="使用代理转发的 IP"
                @update:model-value="save('client-ip')"
              />
            </UiField>
            <UiField
              v-if="settings.client_ip.real_ip_enabled"
              label="IP 请求头"
              :error="errors.header"
              help="填写反向代理传递真实客户端 IP 的请求头名称，例如 X-Real-IP 或 X-Forwarded-For。逗号分隔多个地址时取第一个；只有启用代理转发 IP 后才读取。"
            >
              <UiInput
                v-model="settings.client_ip.real_ip_header"
                placeholder="X-Real-IP"
                @change="save('client-ip')"
              />
            </UiField>
          </div>
        </SettingSection>
        <SettingSection
          id="debug"
          class="settings-half"
          title="日志与调试"
          :saving="saving['debug']"
          :error="saveErrors.debug"
          @save="save('debug')"
          @retry="save('debug')"
        >
          <div class="form-grid settings-debug-grid">
            <UiField
              label="日志目录"
              :error="errors.logPath"
              help="运行 LLM Proxy 的机器上保存 app.log、access.log 和 llm_request_trace.log 的目录。相对路径以服务工作目录为基准；保存后重新装配日志输出，无需重启，已有日志不会自动搬迁。"
            >
              <UiInput v-model="settings.logging.path" mono @change="save('debug')" />
            </UiField>
            <UiField
              label="日志级别"
              help="控制应用日志的详细程度：DEBUG 最详细，INFO 记录常规运行，WARNING 只记录警告及更严重问题，ERROR / CRITICAL 更精简。此项与 LLM 请求调试日志开关独立。"
            >
              <UiSelect
                v-model="settings.logging.level"
                :options="levels"
                @update:model-value="save('debug')"
              />
            </UiField>
            <UiField inline class="settings-toggle" label="LLM 请求调试日志">
              <template #help>
                <p>
                  开启后，将下游请求、转换后的上游请求、上游响应和转换后的下游响应四个阶段写入日志目录的
                  llm_request_trace.log，包括请求行、Header 和 Payload。
                </p>
                <p>
                  用于检查协议转换与转发内容，可能包含完整提示词和凭据，日志体积也会明显增加；关闭后停止记录新的
                  Trace，已有文件保留。
                </p>
              </template>
              <UiSwitch
                v-model="settings.logging.llm_request_debug_enabled"
                label="LLM 请求调试日志"
                @update:model-value="save('debug')"
              />
            </UiField>
          </div>
        </SettingSection>
      </div>
    </template>
    <UiDialog
      :open="!!changedListener"
      :title="changedListener?.restart ? '监听配置已保存' : '监听已更新'"
      :description="
        changedListener?.restart
          ? '当前监听由外部服务管理，请调整服务监听并重启后使用。'
          : changedListener?.url
            ? '新地址已生效，确认后前往新地址。已有请求继续完成。'
            : '新监听已生效。通过反向代理或容器访问时，请同步更新转发设置；仅监听回环地址时需在服务所在机器访问。'
      "
      size="sm"
      :dismissible="false"
    >
      <UiField v-if="changedListener" :label="changedListener.url ? '访问地址' : '监听地址'">
        <UiInput :model-value="changedListener.url || changedListener.address" readonly mono />
      </UiField>
      <template #footer>
        <UiButton variant="primary" @click="continueAfterListenerChange">
          {{ changedListener?.url ? '前往新地址' : '知道了' }}
        </UiButton>
      </template>
    </UiDialog>
    <UiDialog
      :open="!!changedLogin"
      title="登录信息已修改"
      :description="
        changedLogin?.enabled
          ? '请记住修改后的账号密码，确认后将前往登录页。'
          : '管理登录已关闭，确认后将刷新页面。'
      "
      size="sm"
      :dismissible="false"
    >
      <div v-if="changedLogin" class="settings-login-preview">
        <UiField label="已保存账号">
          <UiInput
            :model-value="changedLogin.username"
            readonly
            placeholder="未设置"
            autocomplete="off"
          />
        </UiField>
        <UiField label="已保存密码">
          <UiSecretInput :model-value="changedLogin.password" readonly placeholder="未设置" />
        </UiField>
      </div>
      <template #footer>
        <UiButton variant="primary" @click="continueAfterLoginChange">已记住，继续</UiButton>
      </template>
    </UiDialog>
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
.settings-login-preview {
  display: grid;
  gap: var(--space-4);
}

.settings-panel {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: var(--space-4);
  min-width: 0;
  align-items: start;
}

.settings-half {
  grid-column: span 6;
  container-type: inline-size;
}

.settings-debug-grid {
  grid-template-columns: minmax(140px, 1fr) minmax(100px, 0.6fr) max-content;
  align-items: end;
  column-gap: var(--space-3);
}

.settings-access-grid {
  grid-template-columns: max-content minmax(0, 1fr);
  align-items: end;
  column-gap: var(--space-3);
}

.settings-toggle {
  justify-self: start;
  width: max-content;
  max-width: 100%;
}

@container (width <= 500px) {
  .settings-debug-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .settings-debug-grid > .settings-toggle {
    grid-column: 1 / -1;
  }
}

@media (width <= 700px) {
  .settings-panel {
    grid-template-columns: minmax(0, 1fr);
  }

  .settings-half {
    grid-column: 1 / -1;
  }
}

@container (width <= 360px) {
  .settings-debug-grid,
  .settings-access-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
