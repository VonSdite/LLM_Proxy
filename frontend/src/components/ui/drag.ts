import { ref } from 'vue'
export interface RowMove {
  id: string
  targetId: string
  after: boolean
}
export function useRowDrag(enabled: () => boolean, move: (value: RowMove) => void) {
  const dragging = ref('')
  const dragOver = ref('')
  function endDrag() {
    dragging.value = ''
    dragOver.value = ''
  }
  function startDrag(event: DragEvent, id: string) {
    if (!enabled()) return
    dragging.value = id
    event.dataTransfer?.setData('text/plain', id)
    if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
  }
  function drop(event: DragEvent, targetId: string) {
    event.preventDefault()
    if (enabled() && dragging.value && targetId !== dragging.value) {
      const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
      move({ id: dragging.value, targetId, after: event.clientY > rect.top + rect.height / 2 })
    }
    endDrag()
  }
  function over(event: DragEvent, id: string) {
    if (enabled() && dragging.value) {
      event.preventDefault()
      dragOver.value = id
    }
  }
  return { dragging, dragOver, startDrag, endDrag, drop, over }
}
