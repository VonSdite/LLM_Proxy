import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { UiPagination, UiSelect } from '@/components/ui'

describe('分页条可见性', () => {
  it('总条数超过最小每页条数才显示，切换大页容量仍可切回', async () => {
    const wrapper = mount(UiPagination, { props: { page: 1, pageSize: 50, total: 0 } })
    expect(wrapper.find('.ui-pagination').exists()).toBe(false)
    await wrapper.setProps({ total: 20 })
    expect(wrapper.find('.ui-pagination').exists()).toBe(false)
    await wrapper.setProps({ total: 21 })
    expect(wrapper.find('.ui-pagination').exists()).toBe(true)
    wrapper.getComponent(UiSelect).vm.$emit('update:modelValue', '100')
    expect(wrapper.emitted('update:pageSize')).toEqual([[100]])
    expect(wrapper.emitted('update:page')).toEqual([[1]])
    await wrapper.setProps({ pageSize: 100 })
    expect(wrapper.find('.ui-pagination').exists()).toBe(true)
    await wrapper.setProps({ total: 1 })
    expect(wrapper.find('.ui-pagination').exists()).toBe(false)
  })

  it('使用各页面自己的最小容量，并兼容当前容量更小或选项为空', async () => {
    const wrapper = mount(UiPagination, {
      props: { page: 1, pageSize: 100, sizes: [200, 50, 100], total: 50 },
    })
    expect(wrapper.find('.ui-pagination').exists()).toBe(false)
    await wrapper.setProps({ total: 51 })
    expect(wrapper.find('.ui-pagination').exists()).toBe(true)
    await wrapper.setProps({ pageSize: 10, total: 11 })
    expect(wrapper.find('.ui-pagination').exists()).toBe(true)
    await wrapper.setProps({ sizes: [], total: 10 })
    expect(wrapper.find('.ui-pagination').exists()).toBe(false)
  })
})
