import { nextTick, onBeforeUnmount, onBeforeUpdate, onUpdated, ref, watch } from 'vue'
import './sort.css'

export interface RowMove {
  id: string
  targetId: string
  after: boolean
}
interface SortRow {
  id: string
  group?: string
}

export function useRowDrag(
  enabled: () => boolean,
  rows: () => SortRow[],
  move: (value: RowMove) => void,
) {
  const sortRoot = ref<HTMLElement>()
  const dragging = ref('')
  // 插入线属于拖动瞬态，由本模块维护 DOM，避免移动时触发表格及业务插槽重渲染。
  let target: RowMove | undefined
  let targetElement: HTMLElement | undefined
  let layout: { ids: string[]; elements: HTMLElement[] } | undefined
  let point: { x: number; y: number } | undefined
  let pointer: { id: number; rowId: string; x: number; y: number; handle: HTMLElement } | undefined
  let frame = 0
  let scrollFrame = 0
  let scrollParent: HTMLElement | undefined
  let focusedHandle: HTMLElement | undefined

  function setRoot(element: unknown) {
    sortRoot.value = element instanceof HTMLElement ? element : undefined
  }
  function candidates(id: string) {
    const current = rows()
    const source = current.find((row) => row.id === id)
    return source ? current.filter((row) => row.group === source.group).map((row) => row.id) : []
  }
  function validMove(value: RowMove, ids = candidates(value.id)) {
    const from = ids.indexOf(value.id)
    const to = ids.indexOf(value.targetId)
    return from >= 0 && to >= 0 && from !== to && from !== to + (value.after ? 1 : -1)
  }
  function updateTarget(value: RowMove | undefined) {
    if (target?.targetId === value?.targetId && target?.after === value?.after) return
    targetElement?.removeAttribute('data-drop-position')
    target = value
    targetElement = value
      ? layout?.elements.find((element) => element.dataset.sortId === value.targetId)
      : undefined
    targetElement?.setAttribute('data-drop-position', value!.after ? 'after' : 'before')
  }
  function schedule(x: number, y: number) {
    point = { x, y }
    if (frame) return
    frame = requestAnimationFrame(() => {
      frame = 0
      if (point) updateTarget(locate(point.x, point.y))
    })
  }
  function refresh() {
    if (point && dragging.value) schedule(point.x, point.y)
  }
  function beginDrag(id: string) {
    layout = undefined
    dragging.value = id
    window.addEventListener('scroll', refresh, true)
    window.addEventListener('resize', refresh)
  }
  function locate(x: number, y: number) {
    const root = sortRoot.value
    if (!root || !enabled() || !dragging.value) return undefined
    const bounds = root.getBoundingClientRect()
    if (x < bounds.left || x > bounds.right || y < bounds.top || y > bounds.bottom + 32)
      return undefined
    if (!layout) {
      const ids = candidates(dragging.value)
      const allowed = new Set(ids)
      const elements = [...root.querySelectorAll<HTMLElement>('[data-sort-id]')].filter(
        (element) =>
          element.closest('.ui-sort-root') === root && allowed.has(element.dataset.sortId!),
      )
      layout = { ids, elements }
    }
    const { ids, elements } = layout
    // 行按垂直顺序排列；二分读取当前坐标，滚动后也无需逐行重新测量。
    let low = 0
    let high = elements.length
    while (low < high) {
      const middle = (low + high) >>> 1
      if (y <= elements[middle]!.getBoundingClientRect().bottom) high = middle
      else low = middle + 1
    }
    const element = elements[low] || elements.at(-1)
    if (!element) return undefined
    const rect = element.getBoundingClientRect()
    const value = {
      id: dragging.value,
      targetId: element.dataset.sortId!,
      after: y >= rect.top + rect.height / 2,
    }
    return validMove(value, ids) ? value : undefined
  }
  function endDrag() {
    if (pointer?.handle.hasPointerCapture?.(pointer.id))
      pointer.handle.releasePointerCapture(pointer.id)
    pointer = undefined
    cancelAnimationFrame(frame)
    cancelAnimationFrame(scrollFrame)
    frame = 0
    scrollFrame = 0
    point = undefined
    window.removeEventListener('scroll', refresh, true)
    window.removeEventListener('resize', refresh)
    window.removeEventListener('pointermove', pointerMove)
    window.removeEventListener('pointerup', pointerUp)
    window.removeEventListener('pointercancel', endDrag)
    dragging.value = ''
    updateTarget(undefined)
    layout = undefined
  }
  function finish() {
    const value = target
    endDrag()
    if (enabled() && value && validMove(value)) move(value)
  }
  function startDrag(event: DragEvent, id: string) {
    if (!enabled()) {
      event.preventDefault()
      return
    }
    beginDrag(id)
    event.dataTransfer?.setData('text/plain', id)
    if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
  }
  function over(event: DragEvent) {
    if (!enabled() || !dragging.value) return
    event.preventDefault()
    event.stopPropagation()
    schedule(event.clientX, event.clientY)
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  }
  function drop(event: DragEvent) {
    if (!dragging.value) return
    event.preventDefault()
    event.stopPropagation()
    updateTarget(locate(event.clientX, event.clientY))
    finish()
  }
  function leave(event: DragEvent) {
    if (dragging.value) schedule(event.clientX, event.clientY)
  }
  function scroll() {
    if (!pointer || !dragging.value || !scrollParent) return
    const rect =
      scrollParent === document.scrollingElement
        ? { top: 0, bottom: window.innerHeight }
        : scrollParent.getBoundingClientRect()
    const top = Math.max(0, rect.top)
    const bottom = Math.min(window.innerHeight, rect.bottom)
    const delta =
      pointer.y < top + 40
        ? -Math.min(16, (top + 40 - pointer.y) / 2)
        : pointer.y > bottom - 40
          ? Math.min(16, (pointer.y - bottom + 40) / 2)
          : 0
    if (delta) {
      scrollParent.scrollTop += delta
      schedule(pointer.x, pointer.y)
    }
    scrollFrame = requestAnimationFrame(scroll)
  }
  function startPointer(event: PointerEvent, id: string) {
    if (event.pointerType === 'mouse' || event.button !== 0 || !enabled() || pointer) return
    event.preventDefault()
    const handle = event.currentTarget as HTMLElement
    handle.focus({ preventScroll: true })
    handle.setPointerCapture(event.pointerId)
    pointer = { id: event.pointerId, rowId: id, x: event.clientX, y: event.clientY, handle }
    scrollParent = sortRoot.value?.parentElement || undefined
    while (
      scrollParent &&
      !(
        /(auto|scroll)/.test(getComputedStyle(scrollParent).overflowY) &&
        scrollParent.scrollHeight > scrollParent.clientHeight
      )
    )
      scrollParent = scrollParent.parentElement || undefined
    scrollParent ||= document.scrollingElement as HTMLElement
    window.addEventListener('pointermove', pointerMove, { passive: false })
    window.addEventListener('pointerup', pointerUp)
    window.addEventListener('pointercancel', endDrag)
  }
  function pointerMove(event: PointerEvent) {
    if (!pointer || event.pointerId !== pointer.id) return
    event.preventDefault()
    if (!dragging.value) {
      if (Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y) < 6) return
      beginDrag(pointer.rowId)
      scrollFrame = requestAnimationFrame(scroll)
    }
    pointer.x = event.clientX
    pointer.y = event.clientY
    schedule(pointer.x, pointer.y)
  }
  function pointerUp(event: PointerEvent) {
    if (!pointer || event.pointerId !== pointer.id) return
    if (dragging.value) updateTarget(locate(event.clientX, event.clientY))
    finish()
  }
  function keydown(event: KeyboardEvent, id: string) {
    if (event.key === 'Escape') {
      if (dragging.value || pointer) {
        event.preventDefault()
        event.stopPropagation()
      }
      endDrag()
      return
    }
    if (!enabled() || !['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    const ids = candidates(id)
    const index = ids.indexOf(id)
    const targetId =
      event.key === 'Home'
        ? ids[0]
        : event.key === 'End'
          ? ids.at(-1)
          : ids[index + (event.key === 'ArrowUp' ? -1 : 1)]
    if (!targetId) return
    const value = { id, targetId, after: event.key === 'ArrowDown' || event.key === 'End' }
    if (!validMove(value)) return
    const handle = event.currentTarget as HTMLElement
    move(value)
    void nextTick(() => {
      if (handle.isConnected) {
        handle.focus({ preventScroll: true })
        handle.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
      }
    })
  }
  watch(enabled, (value) => {
    if (!value) endDrag()
  })
  onBeforeUpdate(() => {
    const element = document.activeElement
    focusedHandle =
      element instanceof HTMLElement &&
      element.classList.contains('ui-drag-handle') &&
      sortRoot.value?.contains(element)
        ? element
        : undefined
  })
  onUpdated(() => {
    layout = undefined
    refresh()
    if (focusedHandle?.isConnected && document.activeElement === document.body)
      focusedHandle.focus({ preventScroll: true })
  })
  onBeforeUnmount(endDrag)
  return {
    setRoot,
    dragging,
    startDrag,
    endDrag,
    drop,
    over,
    leave,
    startPointer,
    keydown,
  }
}
