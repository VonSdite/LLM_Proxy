import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it, vi } from 'vitest'
import { EXTENSION_ROUTE_NAME, defineExtensionPage } from '@/app/page-contract'
import { createAppRouter } from '@/app/router'
import { usePageRegistry } from '@/stores/pages'
import { useSession } from '@/stores/session'

function createRouterWithExtension() {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  const pinia = createPinia()
  setActivePinia(pinia)
  useSession(pinia).context = {
    version: 'test',
    username: 'admin',
    auth_enabled: false,
    authenticated: true,
    features: { oauth: true, api_keys: true, model_mapping: true, whitelist: false },
    capabilities: { agents: true },
  }
  usePageRegistry(pinia).register([
    defineExtensionPage({
      id: 'fixture-page',
      title: '扩展验证',
      component: () => import('@/pages/LoginPage.vue'),
    }),
  ])
  return createAppRouter(pinia)
}

describe('管理端路由注册', () => {
  it('内置页面保留现有路径和标题', async () => {
    const router = createRouterWithExtension()
    await router.push('/providers')
    expect(router.currentRoute.value.name).toBe('admin:providers')
    expect(document.title).toBe('Provider · LLM Proxy')

    await router.push('/settings')
    expect(router.currentRoute.value.name).toBe('admin:settings')
    expect(document.title).toBe('系统设置 · LLM Proxy')
  })

  it('扩展页面使用统一宿主路由和页面标题', async () => {
    const router = createRouterWithExtension()
    await router.push('/extensions/fixture-page')
    expect(router.currentRoute.value.name).toBe(EXTENSION_ROUTE_NAME)
    expect(router.currentRoute.value.params.pageId).toBe('fixture-page')
    expect(document.title).toBe('扩展验证 · LLM Proxy')
  })
})
