<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useTheme } from '@/stores/theme'
import UiAlert from './UiAlert.vue'
import { loadAce, type AceEditor } from './ace'
const props = withDefaults(
  defineProps<{
    modelValue: string
    language: 'json' | 'jsonc' | 'toml' | 'yaml' | 'python' | 'text'
    readOnly?: boolean
    label?: string
    height?: string
  }>(),
  { label: '文件内容', height: 'min(58vh, 620px)' },
)
const emit = defineEmits<{ 'update:modelValue': [value: string]; save: [] }>()
const theme = useTheme()
const mount = ref<HTMLElement>()
const error = ref('')
let editor: AceEditor | undefined
let observer: ResizeObserver | undefined
let disposed = false
let changing = false
function setMode() {
  editor?.session.setMode(`ace/mode/${props.language === 'jsonc' ? 'json' : props.language}`)
}
function setTheme() {
  editor?.setTheme(`ace/theme/${theme.theme === 'light' ? 'textmate' : 'tomorrow_night'}`)
}
watch(
  () => props.modelValue,
  (value) => {
    if (!editor || editor.getValue() === value) return
    changing = true
    const cursor = editor.getCursorPosition()
    const scroll = editor.session.getScrollTop()
    editor.setValue(value, -1)
    editor.moveCursorToPosition(cursor)
    editor.session.setScrollTop(scroll)
    changing = false
  },
)
watch(
  () => props.readOnly,
  (value) => editor?.setReadOnly(value),
)
watch(() => props.language, setMode)
watch(() => theme.theme, setTheme)
onMounted(async () => {
  try {
    const ace = await loadAce()
    if (disposed || !mount.value) return
    editor = ace.edit(mount.value)
    editor.setOptions({
      fontSize: 15,
      showPrintMargin: false,
      useWorker: false,
      tabSize: 2,
      wrap: true,
      readOnly: props.readOnly,
    })
    editor.setValue(props.modelValue, -1)
    editor.session.setUseWorker(false)
    setMode()
    setTheme()
    editor.on('change', () => {
      if (!changing && editor) emit('update:modelValue', editor.getValue())
    })
    editor.commands.addCommand({
      name: 'saveConfig',
      bindKey: { win: 'Ctrl-S', mac: 'Command-S' },
      exec: () => {
        if (!props.readOnly) emit('save')
      },
    })
    const input = mount.value.querySelector('textarea')
    input?.setAttribute('aria-label', props.label)
    observer = new ResizeObserver(() => editor?.resize())
    observer.observe(mount.value)
  } catch {
    error.value = '编辑器加载失败，请刷新页面重试'
  }
})
onBeforeUnmount(() => {
  disposed = true
  observer?.disconnect()
  editor?.destroy()
  editor = undefined
})
defineExpose({ focus: () => editor?.focus() })
</script>
<template>
  <div>
    <UiAlert v-if="error">{{ error }}</UiAlert>
    <div ref="mount" class="ui-code-editor" :style="{ height }" />
  </div>
</template>
<style scoped>
.ui-code-editor {
  width: 100%;
  min-height: 180px;
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  overflow: hidden;
}

.ui-code-editor :deep(.ace_gutter-cell),
.ui-code-editor :deep(.ace_gutter-cell_svg-icons) {
  padding-left: 6px;
}
</style>
