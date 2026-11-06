import { mount } from '@vue/test-utils'
import { nextTick, reactive } from 'vue'
import { describe, expect, it } from 'vitest'
import { UiTable } from '@/components/ui'
import { moveItem } from '@/features/shared/order'
const rows = [
  { id: 'a', name: 'Alpha' },
  { id: 'b', name: 'Beta' },
]
const props = {
  rows,
  columns: [{ key: 'name', label: '名称', sortable: true }],
  rowKey: (row: object) => String(Reflect.get(row, 'id')),
}
describe('共享表格', () => {
  it('原位新增和编辑行立即可见，已有行的 DOM 保持稳定', async () => {
    const draft = reactive([{ id: 'a', name: 'Alpha' }])
    const wrapper = mount(UiTable, { props: { ...props, rows: draft } })
    const firstRow = wrapper.find('tbody tr').element
    draft.push({ id: 'b', name: 'Beta' })
    await nextTick()
    expect(wrapper.findAll('tbody tr')).toHaveLength(2)
    draft[0]!.name = 'Edited'
    await nextTick()
    expect(wrapper.find('tbody td').text()).toBe('Edited')
    expect(wrapper.find('tbody tr').element).toBe(firstRow)
    draft.splice(1, 1)
    await nextTick()
    expect(wrapper.findAll('tbody tr')).toHaveLength(1)
    wrapper.unmount()
  })
  it('全选限定当前列表并发送稳定 ID', async () => {
    const wrapper = mount(UiTable, { props: { ...props, selectable: true } })
    await wrapper.find('[role="checkbox"]').trigger('click')
    expect(wrapper.emitted('update:selected')?.[0]).toEqual([['a', 'b']])
    wrapper.unmount()
  })
  it('排序、键盘列宽与移动事件互相独立', async () => {
    const wrapper = mount(UiTable, {
      props: { ...props, reorderable: true, sort: { key: 'name', direction: 'asc' } },
    })
    await wrapper.find('.ui-table-heading').trigger('click')
    expect(wrapper.emitted('update:sort')?.[0]).toEqual([{ key: 'name', direction: 'desc' }])
    const separator = wrapper.find('[role="separator"]')
    await separator.trigger('keydown', { key: 'ArrowRight' })
    expect(separator.attributes('aria-valuenow')).toBe('196')
    await wrapper.find('[aria-label="下移 a"]').trigger('click')
    expect(wrapper.emitted('reorder')?.[0]).toEqual([{ id: 'a', targetId: 'b', after: true }])
    wrapper.unmount()
  })
  it('移动筛选行时保留完整列表中的其他记录', () => {
    expect(
      moveItem(
        ['a', 'hidden', 'b', 'c'],
        { id: 'a', targetId: 'b', after: true },
        (value) => value,
      ),
    ).toEqual(['hidden', 'b', 'a', 'c'])
  })
})
