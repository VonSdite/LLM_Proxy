import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { UiCombobox } from '@/components/ui'

afterEach(() => {
  document.body.innerHTML = ''
})
const options = [
  { value: 'key-value', label: '已有 Key', description: 'sk-…1234' },
  { value: 'another', label: '另一个 Key' },
]
describe('可输入候选', () => {
  it('初始化不会打开菜单或将候选名称写回实际值', async () => {
    const wrapper = mount(UiCombobox, {
      props: { modelValue: 'key-value', options, allowCustom: true, secret: true },
      attachTo: document.body,
    })
    await flushPromises()
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(document.querySelector('[role="option"]')).toBeNull()
    expect((wrapper.get('input').element as HTMLInputElement).value).toBe('key-value')
    wrapper.unmount()
  })

  it('聚焦后保留候选，选择写入原始 Key', async () => {
    const wrapper = mount(UiCombobox, {
      props: { modelValue: '', options, allowCustom: true, secret: true },
      attachTo: document.body,
    })
    await wrapper.get('input').trigger('focus')
    await flushPromises()
    const option = document.querySelector<HTMLElement>('[role="option"]')!
    expect(option.textContent).toContain('已有 Key')
    option.click()
    await flushPromises()
    expect(wrapper.emitted('select')?.at(-1)).toEqual(['key-value'])
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['key-value'])
    wrapper.unmount()
  })

  it('自由输入立即同步原始文本', async () => {
    const wrapper = mount(UiCombobox, { props: { modelValue: '', options, allowCustom: true } })
    await wrapper.get('input').setValue('external/model')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['external/model'])
    wrapper.unmount()
  })
})
