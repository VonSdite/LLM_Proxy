import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { providersApi, type ModelTestResult, type Provider } from '@/api/providers'
import ProviderModels from '@/features/providers/ProviderModels.vue'
import ModelPickerDialog from '@/features/shared/ModelPickerDialog.vue'
import { UiSelect } from '@/components/ui'
import { providerDraft } from '@/features/providers/provider'
const result: ModelTestResult = {
  requested_model: 'model-a',
  available: true,
  first_token_latency_ms: 50,
  tps: 20,
  response_model: 'real-a',
  error: null,
}
afterEach(() => vi.restoreAllMocks())
function harness(values: Partial<Provider> = {}) {
  const provider = {
    ...providerDraft(),
    name: 'test',
    api: 'https://example.test/v1/chat/completions',
    model_list: ['model-a'],
    ...values,
  }
  const wrapper = mount(ProviderModels, { props: { provider, groups: [] } })
  return { wrapper, provider }
}
describe('Provider 模型编辑', () => {
  it('整理前保留输入顺序，显式整理时去空、去重、按名称排序并保留可见项', async () => {
    const { wrapper } = harness({
      model_list: [' b ', 'a', 'b', '', 'Z'],
      hidden_model_list: ['b', 'a'],
    })
    const models = () =>
      wrapper
        .findAll('input[aria-label^="模型 "]')
        .map((input) => (input.element as HTMLInputElement).value)
    expect(models()).toEqual([' b ', 'a', 'b', '', 'Z'])
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '整理')!
      .trigger('click')
    expect(models()).toEqual(['Z', 'a', 'b'])
    expect(wrapper.emitted('change')?.at(-1)).toEqual([['Z', 'a', 'b'], ['a']])
    await wrapper
      .findAll('button')
      .find((button) => button.attributes('aria-label') === '全部显示')!
      .trigger('click')
    expect(wrapper.emitted('change')?.at(-1)).toEqual([['Z', 'a', 'b'], []])
    await wrapper
      .findAll('button')
      .find((button) => button.attributes('aria-label') === '全部隐藏')!
      .trigger('click')
    expect(wrapper.emitted('change')?.at(-1)).toEqual([
      ['Z', 'a', 'b'],
      ['Z', 'a', 'b'],
    ])
    wrapper.unmount()
  })
  it('拉取候选使用当前连接配置，选中追加时保留已有顺序和隐藏状态', async () => {
    const fetch = vi
      .spyOn(providersApi, 'fetchModels')
      .mockResolvedValue({ fetched_models: ['model-b', 'model-a', 'model-b'] })
    const { wrapper, provider } = harness({
      hidden_model_list: ['model-a'],
      proxy_mode: 'custom',
      proxy: 'socks5h://localhost:1080',
      hook: 'sample.py',
    })
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '拉取模型')!
      .trigger('click')
    await flushPromises()
    expect(fetch).toHaveBeenCalledWith(
      expect.objectContaining({ api: provider.api, proxy: provider.proxy, hook: provider.hook }),
      expect.any(AbortSignal),
    )
    const picker = wrapper.findComponent(ModelPickerDialog)
    expect(picker.props('models')).toEqual(['model-b', 'model-a'])
    picker.vm.$emit('apply', ['model-b', 'model-a'])
    await flushPromises()
    expect(wrapper.emitted('change')?.at(-1)).toEqual([['model-a', 'model-b'], ['model-a']])
    wrapper.unmount()
  })
  it.each([{ chosen: ['model-b', 'model-c'] }, { chosen: [] }])(
    '按勾选同步本次候选，保留未拉取模型、既有顺序与隐藏状态：$chosen',
    async ({ chosen }) => {
      vi.spyOn(providersApi, 'fetchModels').mockResolvedValue({
        fetched_models: ['model-c', 'model-b', 'model-a'],
      })
      const { wrapper } = harness({
        model_list: ['manual', 'model-b', 'model-a'],
        hidden_model_list: ['model-a', 'model-b'],
      })
      await wrapper
        .findAll('button')
        .find((button) => button.text() === '拉取模型')!
        .trigger('click')
      await flushPromises()
      const picker = wrapper.findComponent(ModelPickerDialog)
      expect(picker.props('initial')).toEqual(['model-b', 'model-a'])
      picker.vm.$emit('apply', chosen)
      await flushPromises()
      expect(wrapper.emitted('change')).toEqual([
        [['manual', ...chosen], chosen.includes('model-b') ? ['model-b'] : []],
      ])
      expect(wrapper.findComponent(ModelPickerDialog).exists()).toBe(false)
      wrapper.unmount()
    },
  )

  it('拉取期间修改连接配置后丢弃旧候选', async () => {
    let resolve!: (value: { fetched_models: string[] }) => void
    const fetch = vi.spyOn(providersApi, 'fetchModels').mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        }),
    )
    const { wrapper, provider } = harness()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '拉取模型')!
      .trigger('click')
    await wrapper.setProps({ provider: { ...provider, api: 'https://new.example/v1' } })
    expect(fetch.mock.calls[0]![1]!.aborted).toBe(true)
    resolve({ fetched_models: ['old-model'] })
    await flushPromises()
    expect(wrapper.findComponent(ModelPickerDialog).exists()).toBe(false)
    wrapper.unmount()
  })
  it('认证组选择首个启用凭据，手动选择用于后续模型测试', async () => {
    const test = vi.spyOn(providersApi, 'test').mockResolvedValue({ results: [result] })
    const { wrapper, provider } = harness()
    await wrapper.setProps({
      provider: { ...provider, auth_group: 'pool' },
      groups: [
        {
          name: 'pool',
          strategy: 'least_inflight',
          cooldown_seconds_on_429: 60,
          entries: [
            { id: 'off', enabled: false, headers: {} },
            { id: 'on', enabled: true, headers: {} },
          ],
        },
      ],
    })
    const testButton = wrapper.findAll('button').find((button) => button.text() === '测试')!
    await testButton.trigger('click')
    await flushPromises()
    expect(test.mock.calls[0]![0].auth_entry_id).toBe('on')
    wrapper.findComponent(UiSelect).vm.$emit('update:modelValue', 'off')
    await flushPromises()
    await testButton.trigger('click')
    await flushPromises()
    expect(test.mock.calls[1]![0].auth_entry_id).toBe('off')
    wrapper.unmount()
  })
  it('测试发送当前未保存表单快照，并展示性能结果', async () => {
    const test = vi.spyOn(providersApi, 'test').mockResolvedValue({ results: [result] })
    const { wrapper, provider } = harness()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '测试')!
      .trigger('click')
    await flushPromises()
    expect(test).toHaveBeenCalledWith(
      { ...provider, auth_entry_id: '', models: ['model-a'] },
      expect.any(AbortSignal),
    )
    expect(wrapper.text()).toContain('50 ms')
    expect(wrapper.text()).toContain('20 tok/s')
    wrapper.unmount()
  })
  it('正在测试的模型锁定输入，停止后可编辑且丢弃旧响应', async () => {
    let resolve!: (value: { results: ModelTestResult[] }) => void
    vi.spyOn(providersApi, 'test').mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        }),
    )
    const { wrapper } = harness()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '测试')!
      .trigger('click')
    expect(wrapper.find('input[aria-label="模型 1"]').attributes('disabled')).toBeDefined()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '停止测试')!
      .trigger('click')
    expect(wrapper.find('input[aria-label="模型 1"]').attributes('disabled')).toBeUndefined()
    await wrapper.find('input[aria-label="模型 1"]').setValue('model-b')
    resolve({ results: [result] })
    await flushPromises()
    expect(wrapper.text()).not.toContain('50 ms')
    expect(wrapper.emitted('change')?.at(-1)).toEqual([['model-b'], []])
    wrapper.unmount()
  })
  it('不同模型可独立测试，排序后乱序响应仍归属原行，旧请求不解锁新测试', async () => {
    const pending: {
      signal: AbortSignal
      done: (value: { results: ModelTestResult[] }) => void
    }[] = []
    vi.spyOn(providersApi, 'test').mockImplementation(
      (_body, signal) =>
        new Promise((done) => {
          pending.push({ signal: signal!, done })
        }),
    )
    const { wrapper } = harness({ model_list: ['model-a', 'model-b', 'model-c'] })
    const row = (id: string) => wrapper.find(`tr[data-sort-id="${id}"]`)
    const start = (id: string) =>
      row(id)
        .findAll('button')
        .find((button) => button.text() === '测试')!
        .trigger('click')
    await start('1')
    expect(row('1').find('input').attributes('disabled')).toBeDefined()
    expect(row('2').find('input').attributes('disabled')).toBeUndefined()
    await start('2')
    expect(pending).toHaveLength(2)
    expect(pending[0]!.signal.aborted).toBe(false)
    await row('3').find('input').setValue('edited-c')
    wrapper
      .findComponent({ name: 'UiTable' })
      .vm.$emit('reorder', { id: '1', targetId: '3', after: true })
    await flushPromises()
    expect(wrapper.emitted('change')?.at(-1)).toEqual([['model-b', 'edited-c', 'model-a'], []])
    pending[1]!.done({
      results: [{ ...result, requested_model: 'model-b', first_token_latency_ms: 80 }],
    })
    await flushPromises()
    expect(row('2').text()).toContain('80 ms')
    expect(row('1').text()).toContain('测试中')
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '停止测试')!
      .trigger('click')
    await start('1')
    pending[0]!.done({ results: [result] })
    await flushPromises()
    expect(row('1').text()).toContain('测试中')
    expect(row('1').text()).not.toContain('50 ms')
    pending[2]!.done({ results: [{ ...result, first_token_latency_ms: 100 }] })
    await flushPromises()
    expect(row('1').text()).toContain('100 ms')
    expect(row('2').text()).toContain('80 ms')
    wrapper.unmount()
  })
  it('批量测试仅锁正在请求的行，等待行可修改和移除，排序不影响队列', async () => {
    const pending: ((value: { results: ModelTestResult[] }) => void)[] = []
    const api = vi
      .spyOn(providersApi, 'test')
      .mockImplementation(() => new Promise((done) => pending.push(done)))
    const { wrapper } = harness({ model_list: ['model-a', 'model-b', 'model-c'] })
    wrapper.findComponent({ name: 'UiTable' }).vm.$emit('update:selected', ['1', '2', '3'])
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '测试')!
      .trigger('click')
    expect(wrapper.find('input[aria-label="模型 2"]').attributes('disabled')).toBeUndefined()
    await wrapper.find('input[aria-label="模型 2"]').setValue('updated-b')
    await wrapper.find('tr[data-sort-id="3"] button[aria-label="移除模型"]').trigger('click')
    wrapper
      .findComponent({ name: 'UiTable' })
      .vm.$emit('reorder', { id: '1', targetId: '2', after: true })
    pending[0]!({ results: [result] })
    await flushPromises()
    expect(api.mock.calls[1]![0].models).toEqual(['updated-b'])
    pending[1]!({ results: [{ ...result, requested_model: 'updated-b' }] })
    await flushPromises()
    expect(api).toHaveBeenCalledTimes(2)
    expect(wrapper.find('input[aria-label="模型 2"]').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })
  it('上游配置变化及组件卸载均取消测试', async () => {
    const test = vi.spyOn(providersApi, 'test').mockImplementation(() => new Promise(() => {}))
    const { wrapper, provider } = harness()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '测试')!
      .trigger('click')
    const signal = test.mock.calls[0]![1]!
    await wrapper.setProps({ provider: { ...provider, api_key: 'new-key' } })
    expect(signal.aborted).toBe(true)
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '测试')!
      .trigger('click')
    const nextSignal = test.mock.calls[1]![1]!
    wrapper.unmount()
    expect(nextSignal.aborted).toBe(true)
  })
})
