import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { UiSortableList } from '@/components/ui'
const props = {
  items: [{ id: 'a' }, { id: 'b' }, { id: 'c' }],
  rowKey: (row: object) => String(Reflect.get(row, 'id')),
  label: '目标顺序',
}
describe('共享排序列表', () => {
  it('移动按钮发送稳定 ID，禁用边界和保存中的排序', async () => {
    const wrapper = mount(UiSortableList, { props })
    expect((wrapper.get('[aria-label="上移第 1 项"]').element as HTMLButtonElement).disabled).toBe(
      true,
    )
    expect((wrapper.get('[aria-label="下移第 3 项"]').element as HTMLButtonElement).disabled).toBe(
      true,
    )
    await wrapper.get('[aria-label="下移第 1 项"]').trigger('click')
    await wrapper.get('[aria-label="上移第 3 项"]').trigger('click')
    expect(wrapper.emitted('reorder')).toEqual([
      [{ id: 'a', targetId: 'b', after: true }],
      [{ id: 'c', targetId: 'b', after: false }],
    ])
    await wrapper.setProps({ disabled: true })
    expect(
      wrapper.findAll('button').every((button) => (button.element as HTMLButtonElement).disabled),
    ).toBe(true)
    wrapper.unmount()
  })
  it('拖动按落点前后排序，拒绝外部拖入', async () => {
    const wrapper = mount(UiSortableList, { props })
    const destination = wrapper.findAll('[role="listitem"]')[1]!
    vi.spyOn(destination.element, 'getBoundingClientRect').mockReturnValue({
      top: 50,
      height: 40,
    } as DOMRect)
    await destination.trigger('drop', { clientY: 75 })
    expect(wrapper.emitted('reorder')).toBeUndefined()
    const dataTransfer = { setData: vi.fn(), effectAllowed: '' }
    await wrapper.get('[aria-label="拖动第 1 项"]').trigger('dragstart', { dataTransfer })
    await destination.trigger('dragover')
    expect(destination.attributes('data-drag-over')).toBe('true')
    await destination.trigger('drop', { clientY: 75 })
    expect(wrapper.emitted('reorder')).toEqual([[{ id: 'a', targetId: 'b', after: true }]])
    expect(destination.attributes('data-drag-over')).toBeUndefined()
    wrapper.unmount()
  })
})
