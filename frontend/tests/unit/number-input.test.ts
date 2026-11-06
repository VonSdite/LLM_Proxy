import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { UiNumberInput } from '@/components/ui'

describe('数值提交', () => {
  it('输入不提交，失焦只提交一次数值变化', async () => {
    const wrapper = mount(UiNumberInput, { props: { modelValue: 200000 } })
    const input = wrapper.get('input')
    await input.trigger('focus')
    await input.setValue('1000000')
    expect(wrapper.emitted('commit')).toBeUndefined()
    await input.trigger('blur')
    expect(wrapper.emitted('commit')).toEqual([[1000000]])
    await wrapper.setProps({ modelValue: 1000000 })
    await input.trigger('blur')
    expect(wrapper.emitted('commit')).toHaveLength(1)
  })

  it('数值等价的文本不触发提交，精确 Token 值保持不变', async () => {
    const wrapper = mount(UiNumberInput, { props: { modelValue: 60 } })
    await wrapper.get('input').setValue('60.0')
    await wrapper.get('input').trigger('blur')
    expect(wrapper.emitted('commit')).toBeUndefined()
  })

  it('空值和合法零值分别提交', async () => {
    const wrapper = mount(UiNumberInput, { props: { modelValue: 5, min: 0 } })
    await wrapper.get('input').setValue('0')
    await wrapper.get('input').trigger('blur')
    expect(wrapper.emitted('commit')?.[0]).toEqual([0])
    await wrapper.setProps({ modelValue: 0 })
    await wrapper.get('input').setValue('')
    await wrapper.get('input').trigger('blur')
    expect(wrapper.emitted('commit')?.[1]).toEqual([null])
  })

  it('非法数值保留输入并标记错误', async () => {
    const wrapper = mount(UiNumberInput, { props: { modelValue: 10, min: 1, max: 100 } })
    await wrapper.get('input').setValue('hello')
    await wrapper.get('input').trigger('blur')
    expect(wrapper.emitted('commit')).toBeUndefined()
    expect(wrapper.get('input').attributes('aria-invalid')).toBe('true')
    expect((wrapper.get('input').element as HTMLInputElement).value).toBe('hello')
    expect((wrapper.get('input').element as HTMLInputElement).checkValidity()).toBe(false)
    await wrapper.get('input').setValue('20')
    await wrapper.get('input').trigger('keydown', { key: 'Enter' })
    expect((wrapper.get('input').element as HTMLInputElement).checkValidity()).toBe(true)
    expect(wrapper.emitted('commit')).toEqual([[20]])
  })
})
