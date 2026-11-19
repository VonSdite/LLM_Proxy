import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent } from 'vue'
import { describe, expect, it } from 'vitest'
import AppNavigation from '@/app/AppNavigation.vue'
import { defineExtensionPage, EXTENSION_ROUTE_NAME } from '@/app/page-contract'
import { usePageRegistry } from '@/stores/pages'
import { useSession } from '@/stores/session'

async function mountNavigation(path: string) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const session = useSession(pinia)
  session.context = {
    version: 'test',
    username: 'admin',
    auth_enabled: false,
    authenticated: true,
    features: { oauth: true, api_keys: true, model_mapping: true, whitelist: false },
    capabilities: { agents: true },
  }
  usePageRegistry(pinia).register([
    defineExtensionPage({
      id: 'request-tools',
      title: '请求工具',
      navigation: { label: '请求工具', icon: 'terminal', order: 650 },
      component: () => import('@/pages/LoginPage.vue'),
    }),
  ])
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: defineComponent({ template: '<div />' }) },
      {
        path: '/providers',
        name: 'admin:providers',
        component: defineComponent({ template: '<div />' }),
      },
      {
        path: '/extensions/:pageId',
        name: EXTENSION_ROUTE_NAME,
        component: defineComponent({ template: '<div />' }),
      },
      { path: '/:pathMatch(.*)*', component: defineComponent({ template: '<div />' }) },
    ],
  })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(AppNavigation, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return wrapper
}

describe('管理端侧边栏注册表', () => {
  it('内置入口保持原顺序并插入扩展入口', async () => {
    const wrapper = await mountNavigation('/')
    const labels = wrapper.findAll('.app-nav-link').map((link) => link.attributes('aria-label'))
    expect(labels).toEqual([
      'Provider',
      'OAuth',
      '模型映射',
      'API Key',
      'Agent 配置',
      '用户',
      '请求工具',
      '统计',
      '设置',
    ])
    wrapper.unmount()
  })

  it('扩展页面按 pageId 标记当前入口', async () => {
    const wrapper = await mountNavigation('/extensions/request-tools')
    const active = wrapper.get('.app-nav-link.is-active')
    expect(active.attributes('aria-label')).toBe('请求工具')
    expect(active.attributes('aria-current')).toBe('page')
    wrapper.unmount()
  })

  it('Provider 别名路径继续标记对应入口', async () => {
    const wrapper = await mountNavigation('/providers')
    const active = wrapper.get('.app-nav-link.is-active')
    expect(active.attributes('aria-label')).toBe('Provider')
    wrapper.unmount()
  })
})
