import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import OAuthPage from '@/pages/OAuthPage.vue'
import OAuthFileCard from '@/features/oauth/OAuthFileCard.vue'
import { oauthApi, type OAuthFile } from '@/api/oauth'
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
    const wrapper = mount(OAuthPage)
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
