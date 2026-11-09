<script setup lang="ts">
import { nextTick, provide, ref, watch } from 'vue'
import {
  DialogContent,
  DialogDescription,
  DialogOverlay,
  DialogPortal,
  DialogRoot,
  DialogTitle,
} from 'reka-ui'
import UiIconButton from './UiIconButton.vue'
import UiButton from './UiButton.vue'
import { dialogPendingEdits } from './dialogEdits'
const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    description?: string
    size?: 'sm' | 'md' | 'lg' | 'xl'
    placement?: 'center' | 'left' | 'top'
    busy?: boolean
    dirty?: boolean
    save?: () => void | Promise<void>
    saveLabel?: string
    beforeClose?: () => boolean | Promise<boolean>
  }>(),
  { size: 'md', placement: 'center' },
)
const emit = defineEmits<{ 'update:open': [value: boolean]; closed: [] }>()
const closing = ref(false)
const discardPrompt = ref(false)
const body = ref<HTMLElement>()
const pendingEdits = new Set<() => boolean>()
provide(dialogPendingEdits, pendingEdits)
let returnFocus: HTMLElement | null = null
watch(
  () => props.open,
  (open) => {
    if (open)
      returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    else discardPrompt.value = false
  },
  { immediate: true },
)
async function restoreFocus(event: Event) {
  event.preventDefault()
  const target = returnFocus
  // 等待内外弹窗完成卸载，避免焦点被尚未撤销的内层焦点约束拉回。
  await nextTick()
  if (target?.isConnected) target.focus({ preventScroll: true })
}
function focusOnOpen(event: Event) {
  event.preventDefault()
  const dialog = event.target
  if (!(dialog instanceof HTMLElement)) return
  // 优先进入明确指定的输入框或安全操作，否则聚焦弹窗本身。
  const target = dialog.querySelector<HTMLElement>('[autofocus]:not(:disabled)') || dialog
  target.focus({ preventScroll: true })
}
async function requestClose(value = false) {
  if (value || props.busy || closing.value || discardPrompt.value) return
  closing.value = true
  try {
    // 先提交仍在输入中的字段，再比较当前草稿与初始值。
    if (body.value?.contains(document.activeElement)) (document.activeElement as HTMLElement).blur()
    await nextTick()
    if (!props.open || props.busy) return
    if (props.beforeClose && !(await props.beforeClose())) return
    if (props.dirty || [...pendingEdits].some((changed) => changed())) {
      discardPrompt.value = true
      return
    }
    close()
  } finally {
    closing.value = false
  }
}
async function saveAndClose() {
  if (props.busy || !props.save) return
  discardPrompt.value = false
  await nextTick()
  // 校验与保存结果由原表单处理；失败时保留表单和错误说明。
  await props.save()
}
function close() {
  discardPrompt.value = false
  emit('update:open', false)
  emit('closed')
}
function dismiss() {
  if (props.busy || closing.value || discardPrompt.value) return
  close()
}
defineExpose({ requestClose })
</script>
<template>
  <DialogRoot :open="open" @update:open="requestClose">
    <DialogPortal>
      <DialogOverlay class="ui-dialog-overlay" />
      <DialogContent
        class="ui-dialog"
        :class="[
          `ui-dialog--${size}`,
          { 'ui-dialog--drawer': placement === 'left', 'ui-dialog--top': placement === 'top' },
        ]"
        @escape-key-down="busy && $event.preventDefault()"
        @interact-outside="busy && $event.preventDefault()"
        @open-auto-focus="focusOnOpen"
        @close-auto-focus="restoreFocus"
      >
        <header class="ui-dialog-header">
          <div>
            <DialogTitle class="ui-dialog-title">{{ title }}</DialogTitle>
            <DialogDescription v-if="description" class="ui-dialog-description">
              {{ description }}
            </DialogDescription>
            <DialogDescription v-else class="sr-only">{{ title }}</DialogDescription>
          </div>
          <UiIconButton icon="close" label="关闭" :disabled="busy" @click="dismiss" />
        </header>
        <div ref="body" class="ui-dialog-body"><slot /></div>
        <footer v-if="$slots.footer" class="ui-dialog-footer">
          <slot name="footer" :close="dismiss" />
        </footer>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
  <UiDialog
    v-if="discardPrompt"
    v-model:open="discardPrompt"
    title="保存修改？"
    description="关闭前是否保存当前修改？"
    size="sm"
  >
    <template #footer>
      <UiButton autofocus @click="discardPrompt = false">继续编辑</UiButton>
      <UiButton variant="danger-solid" :disabled="busy" @click="!busy && close()">不保存</UiButton>
      <UiButton v-if="save" variant="primary" :disabled="busy" @click="saveAndClose">
        {{ saveLabel || '保存并关闭' }}
      </UiButton>
    </template>
  </UiDialog>
</template>
<style scoped>
.ui-dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-dialog);
  background: var(--backdrop);
}

.ui-dialog {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: var(--z-dialog);
  display: flex;
  flex-direction: column;
  width: min(640px, calc(100vw - 32px));
  max-height: calc(100dvh - 48px);
  border: 1px solid var(--border);
  border-radius: var(--radius-dialog);
  background: var(--surface);
  box-shadow: var(--shadow-dialog);
  outline: none;
}

.ui-dialog--sm {
  width: min(440px, calc(100vw - 32px));
}

.ui-dialog--lg {
  width: min(900px, calc(100vw - 32px));
}

.ui-dialog--xl {
  width: min(1200px, calc(100vw - 32px));
}

.ui-dialog--top {
  --dialog-top-gap: clamp(24px, 10dvh, 96px);

  top: var(--dialog-top-gap);
  max-height: calc(100dvh - var(--dialog-top-gap) - 24px);
  transform: translateX(-50%);
}

.ui-dialog--drawer {
  top: 0;
  left: 0;
  width: min(280px, calc(100vw - 32px));
  height: 100dvh;
  max-height: 100dvh;
  transform: none;
  border-radius: 0 var(--radius-dialog) var(--radius-dialog) 0;
}

.ui-dialog-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-6);
}

.ui-dialog-title {
  color: var(--heading);
  font-size: var(--text-section);
  font-weight: var(--weight-heading);
  padding-top: var(--space-1);
}

.ui-dialog-description {
  color: var(--muted);
  font-size: var(--text-sm);
  margin-top: var(--space-1);
}

.ui-dialog-body {
  padding: var(--space-2) var(--space-6) var(--space-6);
  overflow: auto;
  min-height: 0;
  overscroll-behavior: contain;
}

.ui-dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  flex-wrap: wrap;
  padding: var(--space-4) var(--space-6);
  border-top: 1px solid var(--divider);
}

@media (width <= 600px) {
  .ui-dialog-header,
  .ui-dialog-body,
  .ui-dialog-footer {
    padding-left: var(--space-4);
    padding-right: var(--space-4);
  }
}

.ui-dialog--drawer .ui-dialog-header {
  align-items: center;
  padding: var(--space-4);
}

.ui-dialog--drawer .ui-dialog-title {
  padding-top: 0;
}

.ui-dialog--drawer .ui-dialog-body {
  display: flex;
  flex: 1;
  overflow: hidden;
  padding: var(--space-2) var(--space-3) max(var(--space-4), env(safe-area-inset-bottom));
}
</style>
