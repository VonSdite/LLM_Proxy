import { createPinia } from 'pinia'
import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import StatisticsPage from '@/pages/StatisticsPage.vue'
import { statisticsApi, type UsageRow } from '@/api/statistics'
import { useSession } from '@/stores/session'
import { UiTabs, UiPagination } from '@/components/ui'
import { totals } from '@/features/statistics/statistics'
afterEach(() => vi.restoreAllMocks())
describe('统计页面', () => {
  it('合计成功率按结果次数加权，取消与未知不进入分母', () => {
    const rows = [
      {
        upstream_success_count: 9,
        upstream_error_count: 1,
        upstream_cancelled_count: 20,
        upstream_unknown_count: 30,
      },
      {
        upstream_success_count: 0,
        upstream_error_count: 1,
        upstream_cancelled_count: 0,
        upstream_unknown_count: 0,
      },
    ] as UsageRow[]
    expect(totals(rows, 'stats')?.upstream_success_rate).toBe('81.8%')
    expect(
      totals([{ upstream_cancelled_count: 5, upstream_unknown_count: 8 } as UsageRow], 'stats')
        ?.upstream_success_rate,
    ).toBe('—')
  })
  it('默认加载概览，维度与明细按需请求，功能开关和分页保留', async () => {
    const list = vi.spyOn(statisticsApi, 'list').mockResolvedValue({ rows: [], total: 120 })
    const overview = vi.spyOn(statisticsApi, 'overview').mockResolvedValue({
      totals: {} as UsageRow,
      series: [],
      models: [],
      model_flows: [],
      response_models: [],
      ips: [],
    })
    vi.spyOn(statisticsApi, 'candidates').mockResolvedValue({
      users: [],
      models: [],
      response_models: [],
      target_models: [],
      ip_addresses: [],
    })
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
    expect(list).not.toHaveBeenCalled()
    expect(overview).toHaveBeenCalledTimes(1)
    wrapper.findAllComponents(UiTabs)[0]!.vm.$emit('update:modelValue', 'usage')
    await flushPromises()
    expect(list.mock.calls.at(-1)![0]).toBe('stats')
    expect(wrapper.find('[aria-label="汇总维度"]').text()).not.toContain('API Key')
    session.context.features.api_keys = true
    await flushPromises()
    expect(wrapper.find('[aria-label="汇总维度"]').text()).toContain('API Key')
    wrapper.findAllComponents(UiTabs)[0]!.vm.$emit('update:modelValue', 'logs')
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
