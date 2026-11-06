import { createPinia } from 'pinia'
import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import StatisticsPage from '@/pages/StatisticsPage.vue'
import { statisticsApi } from '@/api/statistics'
import { useSession } from '@/stores/session'
import { UiTabs, UiPagination } from '@/components/ui'
afterEach(() => vi.restoreAllMocks())
describe('统计页面', () => {
  it('默认查询今天，按需加载视图，Key 用量随功能开关显示，换页保留排序', async () => {
    const list = vi.spyOn(statisticsApi, 'list').mockResolvedValue({ rows: [], total: 120 })
    vi.spyOn(statisticsApi, 'candidates').mockResolvedValue({ users: [], models: [] })
    const pinia = createPinia()
    const session = useSession(pinia)
    session.context = {
      version: '2.3.0',
      username: '',
      auth_enabled: false,
      authenticated: true,
      features: { api_keys: false, oauth: true, model_mapping: true, whitelist: false },
      capabilities: { agents: true },
    }
    const wrapper = mount(StatisticsPage, { global: { plugins: [pinia] } })
    await flushPromises()
    expect(list).toHaveBeenCalledTimes(1)
    expect(list.mock.calls[0]![0]).toBe('stats')
    expect(list.mock.calls[0]![1].start_date).toBe(list.mock.calls[0]![1].end_date)
    expect(wrapper.text()).not.toContain('Key 用量')
    session.context.features.api_keys = true
    await flushPromises()
    expect(wrapper.text()).toContain('Key 用量')
    wrapper.getComponent(UiTabs).vm.$emit('update:modelValue', 'logs')
    await flushPromises()
    expect(list.mock.calls.at(-1)![0]).toBe('logs')
    wrapper.getComponent(UiPagination).vm.$emit('update:page', 2)
    await flushPromises()
    expect(list.mock.calls.at(-1)![1]).toMatchObject({
      page: 2,
      sort_key: 'start_time',
      sort_direction: 'desc',
    })
    wrapper.unmount()
  })
})
