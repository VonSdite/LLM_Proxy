import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia } from 'pinia'
import OAuthPage from '@/pages/OAuthPage.vue'
import OAuthFileCard from '@/features/oauth/OAuthFileCard.vue'
import { oauthApi, type OAuthFile } from '@/api/oauth'
import { settingsApi } from '@/api/settings'
import { UiSelect } from '@/components/ui'
function file(name: string, enabled = true): OAuthFile {
  return {
    name,
    path: `/data/${name}`,
    email: 'user@example.test',
    enabled,
    disabled: !enabled,
    plan_type: 'plus',
    availability_status: 'available',
    availability_status_message: '',
    availability_retry_at: null,
    usage_status_message: '',
    expired: '',
    quota: { windows: [{ label: 'Codex', remaining_percent: null, reset_at: null }] },
    quota_error: '',
    quota_refreshed_at: '',
    reset_cards: [
      { id: 'one', expires_at: null },
      { id: 'two', expires_at: null },
    ],
    reset_cards_refreshed_at: '',
  }
}
afterEach(() => vi.restoreAllMocks())
describe('OAuth 页面状态', () => {
  it('更新筛选数量保留按钮 DOM，当前筛选全选不会选择隐藏账号', async () => {
    vi.spyOn(oauthApi, 'files').mockResolvedValue({
      files: [file('a'), file('b', false)],
      total: 2,
      auth_dir: '/data',
    })
    vi.spyOn(settingsApi, 'get').mockResolvedValue({
      server: { host: '127.0.0.1', port: 8080 },
      admin: { username: '', password: '' },
      client_ip: { real_ip_enabled: false, real_ip_header: 'X-Forwarded-For' },
      logging: { path: 'logs', level: 'INFO', llm_request_debug_enabled: false },
      llm_recording: { enabled: false, directory: '/var/lib/llm-proxy/llm-records' },
      oauth: {
        enabled: true,
        proxy_mode: 'direct',
        proxy: '',
        verify_ssl: false,
        safe_desensitization_enabled: false,
      },
      api_keys: { enabled: false },
      model_mapping: { enabled: true },
      auth_enabled: false,
    })
    const wrapper = mount(OAuthPage, { global: { plugins: [createPinia()] } })
    await flushPromises()
    const tabs = wrapper.findAll('[role="tab"]')
    const available = tabs.find((tab) => tab.text().startsWith('可用'))!
    const node = available.element
    expect(tabs[0]!.text()).toBe('可用 1')
    expect(tabs.at(-1)!.text()).toBe('全部 2')
    expect(available.attributes('aria-selected')).toBe('true')
    expect(wrapper.findAllComponents(OAuthFileCard)).toHaveLength(1)
    await wrapper.find('[role="checkbox"]').trigger('click')
    expect(wrapper.findComponent(OAuthFileCard).props('selected')).toBe(true)
    await tabs.find((tab) => tab.text().startsWith('全部'))!.trigger('keydown', { key: 'Enter' })
    expect(wrapper.findAllComponents(OAuthFileCard).map((card) => card.props('selected'))).toEqual([
      true,
      false,
    ])
    expect(
      wrapper.findAll('[role="tab"]').find((tab) => tab.text().startsWith('可用'))!.element,
    ).toBe(node)
    wrapper.unmount()
  })
  it('本期和上期都显示累计费用及对应窗口的预估上限费用', () => {
    const wrapper = mount(OAuthFileCard, {
      props: {
        file: {
          ...file('usage'),
          current_usage: {
            request_count: 12,
            total_tokens: 1200,
            estimated_cost_usd: 3.42,
            estimated_full_cost_usd: 12.21,
            limit_window_seconds: 18000,
          },
          previous_usage: {
            request_count: 8,
            total_tokens: 800,
            estimated_cost_usd: 2.34,
            estimated_full_cost_usd: 34.56,
            limit_window_seconds: 604800,
          },
        },
        selected: false,
        disabled: false,
        loading: false,
        now: Date.now(),
      },
    })
    const rows = wrapper.findAll('.oauth-usage-row')
    expect(rows).toHaveLength(2)
    expect(rows.map((row) => row.findAll('.oauth-usage-values > div').at(-2)!.text())).toEqual([
      '预估费用$3.42',
      '预估费用$2.34',
    ])
    expect(rows.map((row) => row.findAll('.oauth-usage-values > div').at(-1)!.text())).toEqual([
      '5 小时预估$12.21',
      '7 天预估$34.56',
    ])
    wrapper.unmount()
  })
  it('未知额度不显示零，默认选择首张重置卡并保留仍可用的选择', async () => {
    const source = file('a')
    const wrapper = mount(OAuthFileCard, {
      props: { file: source, selected: false, disabled: false, loading: false, now: Date.now() },
    })
    expect(wrapper.text()).not.toContain('0%')
    expect(wrapper.getComponent(UiSelect).props('modelValue')).toBe('one')
    wrapper.getComponent(UiSelect).vm.$emit('update:modelValue', 'two')
    await wrapper.setProps({
      file: { ...source, reset_cards: [...source.reset_cards, { id: 'three', expires_at: null }] },
    })
    expect(wrapper.getComponent(UiSelect).props('modelValue')).toBe('two')
    await wrapper.setProps({ file: { ...source, reset_cards: [{ id: 'one', expires_at: null }] } })
    expect(wrapper.getComponent(UiSelect).props('modelValue')).toBe('one')
    wrapper.unmount()
  })
})
