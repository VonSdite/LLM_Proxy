import { flushPromises, mount, DOMWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { authGroupsApi, providersApi, type ModelTestResult } from '@/api/providers'
import { mappingsApi, type ModelMapping } from '@/api/model-mappings'
import { providerDraft } from '@/features/providers/provider'
import MappingDialog from '@/features/model-mappings/MappingDialog.vue'
import TargetTest from '@/features/model-mappings/TargetTest.vue'
import { UiCombobox, UiSelect } from '@/components/ui'

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})
const provider = {
  ...providerDraft(),
  name: 'region/main',
  api: 'https://example.test/v1',
  model_list: ['org/model'],
}
const mapping: ModelMapping = {
  id: 'smart',
  enabled: true,
  strategy: 'highest_priority',
  cooldown_seconds_on_429: 60,
  safe_desensitization_enabled: false,
  targets: [{ model_id: 'missing/old', priority: 1, enabled: true, status: 'unavailable' }],
}

describe('模型映射目标', () => {
  it('旧的不可用目标可替换，未填写的新行不进入保存载荷', async () => {
    const save = vi.spyOn(mappingsApi, 'save').mockResolvedValue(mapping)
    const wrapper = mount(MappingDialog, {
      props: { mapping, models: ['main/new'] },
      attachTo: document.body,
    })
    await flushPromises()
    wrapper.findComponent(UiCombobox).vm.$emit('update:modelValue', 'main/new')
    const add = [...document.querySelectorAll('button')].find(
      (button) => button.textContent?.trim() === '添加目标',
    )!
    add.click()
    await flushPromises()
    await new DOMWrapper(document.querySelector('#mapping-form')!).trigger('submit')
    await flushPromises()
    expect(save).toHaveBeenCalledWith(
      { ...mapping, targets: [{ model_id: 'main/new', priority: 1, enabled: true }] },
      'smart',
      expect.any(AbortSignal),
    )
    wrapper.unmount()
  })
  it('重复目标阻止提交并在对应字段显示错误', async () => {
    const save = vi.spyOn(mappingsApi, 'save').mockResolvedValue(mapping)
    const wrapper = mount(MappingDialog, {
      props: {
        mapping: {
          ...mapping,
          targets: [
            { model_id: 'main/a', enabled: true, priority: 1 },
            { model_id: 'main/a', enabled: true, priority: 0 },
          ],
        },
        models: ['main/a'],
      },
      attachTo: document.body,
    })
    await flushPromises()
    await new DOMWrapper(document.querySelector('#mapping-form')!).trigger('submit')
    expect(document.body.textContent).toContain('目标模型重复')
    expect(save).not.toHaveBeenCalled()
    wrapper.unmount()
  })
  it('测试保留完整上游 ID，未知性能指标不显示为零', async () => {
    vi.spyOn(providersApi, 'list').mockResolvedValue([provider])
    const test = vi.spyOn(providersApi, 'test').mockResolvedValue({
      results: [
        {
          requested_model: 'org/model',
          available: true,
          first_token_latency_ms: null,
          tps: null,
          response_model: 'org/model',
          error: null,
        },
      ],
    })
    const wrapper = mount(TargetTest, { props: { model: 'region/main/org/model' } })
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(test).toHaveBeenCalledWith(
      expect.objectContaining({ models: ['org/model'], name: 'region/main' }),
      expect.any(AbortSignal),
    )
    expect(wrapper.text()).toContain('— · —')
    expect(wrapper.text()).not.toContain('0 ms')
    wrapper.unmount()
  })
  it('Auth Group 默认选择首个启用条目，后续测试保留手动选择', async () => {
    vi.spyOn(providersApi, 'list').mockResolvedValue([{ ...provider, auth_group: 'pool' }])
    vi.spyOn(authGroupsApi, 'get').mockResolvedValue({
      name: 'pool',
      strategy: 'least_inflight',
      cooldown_seconds_on_429: 60,
      entries: [
        { id: 'off', enabled: false, headers: {} },
        { id: 'on', enabled: true, headers: {} },
      ],
    })
    const test = vi.spyOn(providersApi, 'test').mockResolvedValue({ results: [] })
    const wrapper = mount(TargetTest, { props: { model: 'region/main/org/model' } })
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(test.mock.calls[0]![0].auth_entry_id).toBe('on')
    wrapper.findComponent(UiSelect).vm.$emit('update:modelValue', 'off')
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '测试')!
      .trigger('click')
    await flushPromises()
    expect(test.mock.calls[1]![0]).toMatchObject({ auth_entry_id: 'off', api_key: '' })
    wrapper.unmount()
  })
  it('OAuth 目标显示不支持测试，不伪造失败状态', async () => {
    vi.spyOn(providersApi, 'list').mockResolvedValue([])
    const test = vi.spyOn(providersApi, 'test')
    const wrapper = mount(TargetTest, { props: { model: 'codex/gpt-5' } })
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('性能测试支持 Provider 目标')
    expect(wrapper.text()).not.toContain('失败')
    expect(test).not.toHaveBeenCalled()
    wrapper.unmount()
  })
  it('编辑目标或卸载时取消测试，迟到响应不能覆盖当前行', async () => {
    vi.spyOn(providersApi, 'list').mockResolvedValue([provider])
    let resolve!: (value: { results: ModelTestResult[] }) => void
    const test = vi.spyOn(providersApi, 'test').mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        }),
    )
    const wrapper = mount(TargetTest, { props: { model: 'region/main/org/model' } })
    await wrapper.get('button').trigger('click')
    await flushPromises()
    const signal = test.mock.calls[0]![1]!
    await wrapper.setProps({ model: 'main/changed' })
    expect(signal.aborted).toBe(true)
    resolve({
      results: [
        {
          requested_model: 'org/model',
          available: true,
          first_token_latency_ms: 12,
          tps: 34,
          response_model: 'org/model',
          error: null,
        },
      ],
    })
    await flushPromises()
    expect(wrapper.text()).not.toContain('12 ms')
    wrapper.unmount()
  })
})
