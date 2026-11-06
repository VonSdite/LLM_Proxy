import { mount, flushPromises } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { UiMultiSelect, UiField, UiInput } from '@/components/ui'
import { defineComponent } from 'vue'
describe('共享多选与字段标签', () => {
  it('字段标题关联多选触发按钮，浮层搜索使用独立标签', async () => {
    const wrapper = mount(
      defineComponent({
        components: { UiField, UiMultiSelect },
        template:
          '<UiField label="请求模型"><UiMultiSelect :model-value="[]" :options="[]" /></UiField>',
      }),
      { attachTo: document.body },
    )
    const label = wrapper.get('label')
    const trigger = wrapper.get('button')
    expect(trigger.attributes('id')).toBe(label.attributes('for'))
    expect(trigger.attributes('aria-labelledby')).toBe(label.attributes('id'))
    await trigger.trigger('click')
    await flushPromises()
    const search = document.querySelector<HTMLInputElement>('input[aria-label="搜索选项"]')!
    expect(search.id).not.toBe(trigger.attributes('id'))
    wrapper.unmount()
  })
  it('筛选后的全选只改变当前候选，保留其他选中项', async () => {
    const wrapper = mount(UiMultiSelect, {
      props: {
        modelValue: ['other'],
        options: [
          { value: 'a1', label: 'Alpha 1' },
          { value: 'a2', label: 'Alpha 2' },
          { value: 'b', label: 'Beta' },
        ],
      },
      attachTo: document.body,
    })
    await wrapper.get('button').trigger('click')
    await flushPromises()
    const input = document.querySelector<HTMLInputElement>('input[aria-label="搜索选项"]')!
    input.value = 'Alpha'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()
    ;(document.querySelector('[role="checkbox"]') as HTMLElement).click()
    await flushPromises()
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([['other', 'a1', 'a2']])
    wrapper.unmount()
  })
  it('分组字段不为多个输入重复分配 ID，普通字段关联正确控件', () => {
    const wrapper = mount(
      defineComponent({
        components: { UiField, UiInput },
        template:
          '<div><UiField label="Headers" group><UiInput aria-label="Header 名称" /><UiInput aria-label="Header 值" /></UiField><UiField label="名称"><UiInput /></UiField></div>',
      }),
    )
    const inputs = wrapper.findAll('input')
    expect(inputs[0]!.attributes('id')).toBeUndefined()
    expect(inputs[1]!.attributes('id')).toBeUndefined()
    expect(wrapper.get('label').attributes('for')).toBe(inputs[2]!.attributes('id'))
    expect(wrapper.get('[role="group"]').attributes('aria-labelledby')).toBeTruthy()
    wrapper.unmount()
  })
})
