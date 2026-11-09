import { mount } from '@vue/test-utils'
import { nextTick, reactive } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
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
afterEach(() => vi.restoreAllMocks())
describe('共享表格', () => {
  it('快速拖动按帧合并、复用行索引且不重渲染单元格，松手采用最终位置', async () => {
    const frames = new Map<number, FrameRequestCallback>()
    let sequence = 0
    vi.spyOn(globalThis, 'requestAnimationFrame').mockImplementation((callback) => {
      frames.set(++sequence, callback)
      return sequence
    })
    vi.spyOn(globalThis, 'cancelAnimationFrame').mockImplementation((id) => {
      frames.delete(id)
    })
    const draw = async () => {
      const pending = [...frames.values()]
      frames.clear()
      pending.forEach((callback) => callback(0))
      await nextTick()
    }
    const cell = vi.fn(({ value }: { value: unknown }) => String(value))
    const wrapper = mount(UiTable, {
      props: {
        ...props,
        reorderable: true,
        rows: Array.from({ length: 128 }, (_, i) => ({ id: String(i), name: `Provider ${i}` })),
      },
      slots: { 'cell-name': cell },
    })
    vi.spyOn(wrapper.element, 'getBoundingClientRect').mockReturnValue({
      top: 0,
      bottom: 5120,
      left: 0,
      right: 200,
    } as DOMRect)
    let scrollOffset = 0
    const measure = vi.fn(
      (index: number) =>
        ({
          top: index * 40 - scrollOffset,
          bottom: (index + 1) * 40 - scrollOffset,
          height: 40,
        }) as DOMRect,
    )
    wrapper
      .findAll('tbody tr')
      .forEach((row, i) =>
        vi.spyOn(row.element, 'getBoundingClientRect').mockImplementation(() => measure(i)),
      )
    const scan = vi.spyOn(wrapper.element, 'querySelectorAll')
    await wrapper.get('[aria-label="拖动 0 排序"]').trigger('dragstart')
    const renders = cell.mock.calls.length
    const root = wrapper.get('.ui-sort-root')
    for (let i = 1; i <= 60; i++)
      await root.trigger('dragover', { clientX: 10, clientY: i * 40 + 30 })
    expect(frames.size).toBe(1)
    expect(measure).not.toHaveBeenCalled()
    await draw()
    expect(wrapper.get('[data-sort-id="60"]').attributes('data-drop-position')).toBe('after')
    expect(measure.mock.calls.length).toBeLessThanOrEqual(10)
    const scans = scan.mock.calls.filter(([selector]) => selector === '[data-sort-id]').length
    expect(scans).toBe(1)
    for (let i = 61; i <= 90; i++)
      await root.trigger('dragover', { clientX: 10, clientY: i * 40 + 30 })
    await draw()
    expect(wrapper.get('[data-sort-id="60"]').attributes('data-drop-position')).toBeUndefined()
    expect(wrapper.get('[data-sort-id="90"]').attributes('data-drop-position')).toBe('after')
    expect(measure.mock.calls.length).toBeLessThanOrEqual(20)
    expect(scan.mock.calls.filter(([selector]) => selector === '[data-sort-id]')).toHaveLength(1)
    expect(cell).toHaveBeenCalledTimes(renders)
    scrollOffset = 40
    window.dispatchEvent(new Event('scroll'))
    await draw()
    expect(wrapper.get('[data-sort-id="91"]').attributes('data-drop-position')).toBe('after')
    await root.trigger('dragover', { clientX: 10, clientY: 4010 })
    await root.trigger('drop', { clientX: 10, clientY: 5130 })
    expect(wrapper.emitted('reorder')).toEqual([[{ id: '0', targetId: '127', after: true }]])
    expect(frames.size).toBe(0)
    expect(wrapper.find('[data-drop-position]').exists()).toBe(false)
    wrapper.unmount()
  })
  it('等待操作期间保留顺序列和行节点，禁止拖动与移动', async () => {
    const wrapper = mount(UiTable, { props: { ...props, reorderable: true } })
    const headers = wrapper.findAll('th').map((header) => header.element)
    const row = wrapper.find('tbody tr').element
    await wrapper.setProps({ reorderDisabled: true })
    expect(wrapper.findAll('th').map((header) => header.element)).toEqual(headers)
    expect(wrapper.find('tbody tr').element).toBe(row)
    expect(wrapper.find('.ui-drag-handle').attributes('draggable')).toBe('false')
    await wrapper.find('[aria-label="拖动 a 排序"]').trigger('keydown', { key: 'ArrowDown' })
    expect(wrapper.emitted('reorder')).toBeUndefined()
    await wrapper.setProps({ reorderDisabled: false })
    await wrapper.find('[aria-label="拖动 a 排序"]').trigger('keydown', { key: 'ArrowDown' })
    expect(wrapper.emitted('reorder')).toHaveLength(1)
    wrapper.unmount()
  })

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
    await wrapper.find('[aria-label="拖动 a 排序"]').trigger('keydown', { key: 'ArrowDown' })
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
