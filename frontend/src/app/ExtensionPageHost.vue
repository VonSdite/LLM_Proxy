<script setup lang="ts">
import { computed, markRaw, ref, shallowRef, watch, type Component } from 'vue'
import { useRoute } from 'vue-router'
import { UiAlert, UiButton, UiEmptyState } from '@/components/ui'
import { useLifetime } from '@/features/shared/useLifetime'
import { usePageRegistry } from '@/stores/pages'

const route = useRoute()
const pages = usePageRegistry()
const lifetime = useLifetime()
const page = computed(() =>
  pages.extensionPage(typeof route.params.pageId === 'string' ? route.params.pageId : ''),
)
const pageId = computed(() => page.value?.id || '')
const component = shallowRef<Component>()
const loading = ref(false)
const error = ref('')
let generation = 0

/** 加载扩展组件；切换页面后忽略过期结果。 */
async function load() {
  const selected = page.value
  generation += 1
  const current = generation
  component.value = undefined
  error.value = ''
  loading.value = false
  if (!selected) return
  loading.value = true
  try {
    const module = await selected.component()
    if (current !== generation || lifetime.aborted) return
    if (!module.default) throw new Error('扩展页面缺少默认导出的 Vue 组件')
    component.value = markRaw(module.default)
  } catch (reason) {
    if (current !== generation || lifetime.aborted) return
    error.value = reason instanceof Error ? reason.message : '扩展页面加载失败'
  } finally {
    if (current === generation && !lifetime.aborted) loading.value = false
  }
}

watch(
  () => page.value?.id,
  () => {
    void load()
  },
  { immediate: true },
)
</script>

<template>
  <component :is="component" v-if="component" :key="pageId" />
  <div v-else-if="loading" class="page"><UiEmptyState loading /></div>
  <div v-else-if="page" class="page extension-page-error">
    <UiAlert>扩展页面加载失败：{{ error }}</UiAlert>
    <UiButton @click="load">重试</UiButton>
  </div>
  <div v-else class="page">
    <UiEmptyState title="扩展页面不存在" description="请检查扩展目录并重新构建管理界面。" />
  </div>
</template>

<style scoped>
.extension-page-error {
  display: grid;
  justify-items: start;
  gap: var(--space-3);
}
</style>
