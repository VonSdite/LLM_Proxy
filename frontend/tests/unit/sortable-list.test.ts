import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { UiSortableList } from '@/components/ui'
const props = {
  items: [{ id: 'a' }, { id: 'b' }, { id: 'c' }],
  rowKey: (row: object) => String(Reflect.get(row, 'id')),
  label: '目标顺序',
}
describe('共享排序列表', () => {
  it('统一把手支持键盘移动和首尾定位，边界与保存期间不发送排序', async () => {
    const wrapper = mount(UiSortableList, { props })
    expect(wrapper.findAll('button')).toHaveLength(3)
    const first = wrapper.get('[aria-label="拖动第 1 项"]')
    const last = wrapper.get('[aria-label="拖动第 3 项"]')
    await first.trigger('keydown', { key: 'ArrowUp' })
    await last.trigger('keydown', { key: 'ArrowDown' })
    expect(wrapper.emitted('reorder')).toBeUndefined()
    await first.trigger('keydown', { key: 'ArrowDown' })
    await last.trigger('keydown', { key: 'ArrowUp' })
    await first.trigger('keydown', { key: 'End' })
    await last.trigger('keydown', { key: 'Home' })
    expect(wrapper.emitted('reorder')).toEqual([
      [{ id: 'a', targetId: 'b', after: true }],
      [{ id: 'c', targetId: 'b', after: false }],
      [{ id: 'a', targetId: 'c', after: true }],
      [{ id: 'c', targetId: 'a', after: false }],
    ])
    await wrapper.setProps({ disabled: true })
    await first.trigger('keydown', { key: 'End' })
    expect(wrapper.emitted('reorder')).toHaveLength(4)
    expect(
      wrapper.findAll('button').every((button) => button.attributes('aria-disabled') === 'true'),
    ).toBe(true)
    wrapper.unmount()
  })
  it('拖动按落点前后排序，拒绝外部拖入', async () => {
    const wrapper = mount(UiSortableList, { props })
    vi.spyOn(wrapper.element, 'getBoundingClientRect').mockReturnValue({
      top: 0,
      bottom: 140,
      left: 0,
      right: 200,
    } as DOMRect)
    wrapper.findAll('[role="listitem"]').forEach((item, index) => {
      vi.spyOn(item.element, 'getBoundingClientRect').mockReturnValue({
        top: index * 50,
        bottom: index * 50 + 40,
        height: 40,
      } as DOMRect)
    })
    const destination = wrapper.findAll('[role="listitem"]')[1]!
    await destination.trigger('drop', { clientX: 10, clientY: 75 })
    expect(wrapper.emitted('reorder')).toBeUndefined()
    const dataTransfer = { setData: vi.fn(), effectAllowed: '' }
    await wrapper.get('[aria-label="拖动第 1 项"]').trigger('dragstart', { dataTransfer })
    await destination.trigger('dragover', { clientX: 10, clientY: 75 })
    await new Promise(requestAnimationFrame)
    expect(destination.attributes('data-drop-position')).toBe('after')
    await destination.trigger('drop', { clientX: 10, clientY: 75 })
    expect(wrapper.emitted('reorder')).toEqual([[{ id: 'a', targetId: 'b', after: true }]])
    expect(destination.attributes('data-drop-position')).toBeUndefined()
    await wrapper.get('[aria-label="拖动第 1 项"]').trigger('dragstart', { dataTransfer })
    await wrapper.get('.ui-sort-tail').trigger('dragover', { clientX: 10, clientY: 155 })
    await new Promise(requestAnimationFrame)
    expect(wrapper.findAll('[role="listitem"]')[2]!.attributes('data-drop-position')).toBe('after')
    await wrapper.get('.ui-sort-tail').trigger('drop', { clientX: 10, clientY: 155 })
    expect(wrapper.emitted('reorder')?.at(-1)).toEqual([{ id: 'a', targetId: 'c', after: true }])
    await wrapper.get('[aria-label="拖动第 3 项"]').trigger('dragstart', { dataTransfer })
    await destination.trigger('dragover', { clientX: 10, clientY: 55 })
    await new Promise(requestAnimationFrame)
    expect(destination.attributes('data-drop-position')).toBe('before')
    await wrapper.setProps({ disabled: true })
    await destination.trigger('drop', { clientX: 10, clientY: 55 })
    expect(wrapper.emitted('reorder')).toHaveLength(2)
    wrapper.unmount()
  })
})
