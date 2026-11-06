import { defineComponent, h, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ModelPermissionPicker from '@/features/shared/ModelPermissionPicker.vue'
import ModelPickerDialog from '@/features/shared/ModelPickerDialog.vue'
import type { ModelPermissions } from '@/api/permissions'

function harness(initial: ModelPermissions, models = ['alpha', 'beta', 'gamma']) {
  const value = ref(initial)
  const wrapper = mount(
    defineComponent({
      setup: () => () =>
        h(ModelPermissionPicker, {
          modelValue: value.value,
          models,
          'onUpdate:modelValue': (next) => {
            value.value = next
          },
        }),
    }),
  )
  return { wrapper, value }
}
describe('共享模型权限', () => {
  it('筛选全选仅影响当前筛选，保留其他模型的授权', async () => {
    const { wrapper, value } = harness(['beta'])
    await wrapper.find('input').setValue('alpha')
    await wrapper.findAll('[role="checkbox"]')[0]!.trigger('click')
    expect(value.value).toEqual(['beta', 'alpha'])
    await wrapper.findAll('[role="checkbox"]')[0]!.trigger('click')
    expect(value.value).toEqual(['beta'])
    wrapper.unmount()
  })
  it('全部模型与指定模型切换保留手工选择', async () => {
    const { wrapper, value } = harness(['beta'])
    await wrapper.findAll('[role="tab"]')[0]!.trigger('keydown', { key: 'Enter' })
    expect(value.value).toBe('*')
    await wrapper.findAll('[role="tab"]')[1]!.trigger('keydown', { key: 'Enter' })
    expect(value.value).toEqual(['beta'])
    wrapper.unmount()
  })
  it('目录中暂时不存在的已授权模型仍可查看和移除', async () => {
    const { wrapper, value } = harness(['old-model'])
    expect(wrapper.text()).toContain('当前不可用')
    await wrapper.findAll('[role="checkbox"]').at(-1)!.trigger('click')
    expect(value.value).toEqual([])
    wrapper.unmount()
  })
  it('拉取候选模型按筛选选择，提交原始完整 ID', async () => {
    const wrapper = mount(ModelPickerDialog, {
      props: { title: '添加模型', models: ['vendor/a', 'vendor/b'], initial: ['vendor/b'] },
      global: { stubs: { UiDialog: { template: '<div><slot/><slot name="footer"/></div>' } } },
    })
    await wrapper.find('input').setValue('/a')
    await wrapper.findAll('[role="checkbox"]')[0]!.trigger('click')
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '添加所选')!
      .trigger('click')
    await flushPromises()
    expect(wrapper.emitted('apply')?.[0]?.[0]).toEqual(['vendor/a', 'vendor/b'])
    wrapper.unmount()
  })
})
