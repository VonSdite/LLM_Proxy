<script setup lang="ts">
import { ref } from 'vue'
import { usersApi } from '@/api/users'
import type { ModelPermissions } from '@/api/permissions'
import { errorMessage } from '@/api/client'
import { useLifetime } from '@/features/shared/useLifetime'
import ModelPermissionPicker from '@/features/shared/ModelPermissionPicker.vue'
import { UiAlert, UiButton, UiDialog } from '@/components/ui'
const props = defineProps<{ ids: number[]; models: string[] }>()
const emit = defineEmits<{ close: []; saved: [] }>()
const permissions = ref<ModelPermissions>('*')
const busy = ref(false)
const error = ref('')
const signal = useLifetime()
async function submit() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await usersApi.batch(props.ids, 'set_model_permissions', permissions.value, signal)
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
    :title="`设置 ${ids.length} 个用户的模型权限`"
    :busy="busy"
    @update:open="emit('close')"
  >
    <div class="stack">
      <UiAlert v-if="error">{{ error }}</UiAlert>
      <ModelPermissionPicker v-model="permissions" :models="models" :disabled="busy" />
    </div>
    <template #footer>
      <UiButton :disabled="busy" @click="emit('close')">取消</UiButton>
      <UiButton variant="primary" :loading="busy" @click="submit">保存</UiButton>
    </template>
  </UiDialog>
</template>
