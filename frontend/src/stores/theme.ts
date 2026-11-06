import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

export const useTheme = defineStore('theme', () => {
  const theme = ref<'light' | 'dark'>(
    document.documentElement.dataset.theme === 'light' ? 'light' : 'dark',
  )
  watch(
    theme,
    (value) => {
      document.documentElement.dataset.theme = value
      try {
        localStorage.setItem('theme', value)
      } catch {
        /* 隐私模式下使用当前会话主题。 */
      }
    },
    { immediate: true },
  )
  function toggle() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
  }
  return { theme, toggle }
})
