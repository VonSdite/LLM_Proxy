import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent, type Component } from 'vue'
import { describe, expect, it } from 'vitest'
import ExtensionPageHost from '@/app/ExtensionPageHost.vue'
import { defineExtensionPage, type PageComponentLoader } from '@/app/page-contract'
import { usePageRegistry } from '@/stores/pages'

const fixturePage = defineComponent({ template: '<div>扩展页面内容</div>' })

function createHarness(loader?: PageComponentLoader) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const registry = usePageRegistry(pinia)
  if (loader) {
    registry.register([
      defineExtensionPage({
        id: 'fixture-page',
        title: '扩展验证',
        component: loader,
      }),
    ])
  }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/extensions/:pageId',
        name: 'admin:extension',
        component: ExtensionPageHost,
      },
    ],
  })
  return { pinia, router }
}

async function mountHost(path: string, loader?: PageComponentLoader) {
  const { pinia, router } = createHarness(loader)
  await router.push(path)
  await router.isReady()
  const wrapper = mount(ExtensionPageHost, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return wrapper
}

describe('扩展页面宿主', () => {
  it('按扩展 ID 异步加载页面组件', async () => {
    const wrapper = await mountHost('/extensions/fixture-page', () =>
      Promise.resolve({ default: fixturePage as Component }),
    )
    expect(wrapper.text()).toContain('扩展页面内容')
    wrapper.unmount()
  })

  it('未知扩展显示稳定空状态', async () => {
    const wrapper = await mountHost('/extensions/missing-page')
    expect(wrapper.text()).toContain('扩展页面不存在')
    wrapper.unmount()
  })

  it('组件加载失败显示错误和重试入口', async () => {
    const wrapper = await mountHost('/extensions/fixture-page', () =>
      Promise.reject(new Error('fixture chunk unavailable')),
    )
    expect(wrapper.text()).toContain('扩展页面加载失败')
    expect(wrapper.text()).toContain('fixture chunk unavailable')
    expect(wrapper.get('button').text()).toBe('重试')
    wrapper.unmount()
  })
})
