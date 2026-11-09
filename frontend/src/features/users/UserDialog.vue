<script setup lang="ts">
import { reactive, ref } from 'vue'
import { usersApi, type User } from '@/api/users'
import type { ModelPermissions } from '@/api/permissions'
import { errorMessage } from '@/api/client'
import { useLifetime } from '@/features/shared/useLifetime'
import { useDraftChanges } from '@/features/shared/useDraftChanges'
import ModelPermissionPicker from '@/features/shared/ModelPermissionPicker.vue'
import { UiAlert, UiButton, UiDialog, UiField, UiInput, UiSwitch } from '@/components/ui'
const props = defineProps<{ user?: User; models: string[]; whitelist: boolean }>()
const emit = defineEmits<{ close: []; saved: [] }>()
const draft = reactive({
  username: props.user?.username || '',
  ip_address: props.user?.ip_address || '',
  whitelist_access_enabled: Boolean(props.user?.whitelist_access_enabled),
  model_permissions: (props.user?.model_permissions || '*') as ModelPermissions,
})
const errors = reactive<Record<string, string>>({})
const error = ref('')
const busy = ref(false)
const signal = useLifetime()
async function submit() {
  errors.username = draft.username.trim() ? '' : '请输入用户名'
  errors.ip_address = draft.ip_address.trim() ? '' : '请输入 IP 地址'
  if (busy.value || Object.values(errors).some(Boolean)) return
  busy.value = true
  error.value = ''
  try {
    if (props.user) await usersApi.update(props.user.id, draft, signal)
    else
      await usersApi.create(
        {
          username: draft.username.trim(),
          ip_address: draft.ip_address.trim(),
        },
        signal,
      )
    if (signal.aborted) return
    emit('saved')
    emit('close')
  } catch (reason) {
    if (signal.aborted) return
    error.value = errorMessage(reason)
    if (/IP|地址/i.test(error.value)) errors.ip_address = error.value
    else if (/用户|username/i.test(error.value)) errors.username = error.value
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
    open
    :title="user ? '编辑用户' : '添加用户'"
    :busy="busy"
    :dirty="dirty"
    @update:open="emit('close')"
  >
    <form id="user-form" class="stack" @submit.prevent="submit">
      <UiAlert v-if="error">{{ error }}</UiAlert>
      <div class="form-grid">
        <UiField
          label="用户名"
          required
          :error="errors.username"
          help="用于识别与汇总此 IP 的调用记录，不是后台登录账号。后台登录在系统设置中单独配置。"
        >
          <UiInput v-model="draft.username" autofocus :disabled="busy" />
        </UiField>
        <UiField
          label="IP 地址"
          required
          :error="errors.ip_address"
          help="填写单个 IPv4 或 IPv6 地址，不支持网段 CIDR。匹配服务识别到的客户端 IP；经过反向代理时请同时检查系统设置中的客户端 IP 规则。"
        >
          <UiInput v-model="draft.ip_address" placeholder="IPv4 / IPv6" mono :disabled="busy" />
        </UiField>
      </div>
      <template v-if="user && whitelist">
        <UiSwitch v-model="draft.whitelist_access_enabled" :disabled="busy">
          允许白名单访问
        </UiSwitch>
        <UiField label="模型权限" group>
          <ModelPermissionPicker
            v-model="draft.model_permissions"
            :models="models"
            :disabled="busy"
          />
        </UiField>
      </template>
    </form>
    <template #footer="{ close }">
      <UiButton :disabled="busy" @click="close()">取消</UiButton>
      <UiButton variant="primary" type="submit" form="user-form" :loading="busy">保存</UiButton>
    </template>
  </UiDialog>
</template>
