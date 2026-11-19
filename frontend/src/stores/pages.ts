import { computed, shallowRef } from 'vue'
import { defineStore } from 'pinia'
import { builtinPages } from '@/app/builtin-pages'
import { localExtensionPages } from '@/app/extension-pages'
import {
  findExtensionPage,
  sortNavigationPages,
  validatePageDefinitions,
} from '@/app/page-registry'
import type { AdminPageDefinition } from '@/app/page-contract'

export const usePageRegistry = defineStore('admin-pages', () => {
  const pages = shallowRef<AdminPageDefinition[]>([])

  /** 注册页面描述；重复 ID、路径冲突或非法路径会直接终止注册。 */
  function register(definitions: AdminPageDefinition[]) {
    pages.value = validatePageDefinitions([...pages.value, ...definitions])
  }

  register([...builtinPages, ...localExtensionPages])

  const navigationPages = computed(() => sortNavigationPages(pages.value))

  function extensionPage(id: string) {
    return findExtensionPage(pages.value, id)
  }

  return { pages, register, navigationPages, extensionPage }
})
