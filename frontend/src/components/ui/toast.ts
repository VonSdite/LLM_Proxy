import { shallowRef } from 'vue'

export interface Toast {
  id: number
  message: string
  variant: 'success' | 'danger' | 'info' | 'warning'
}
export const toasts = shallowRef<Toast[]>([])
let nextId = 0
export function notify(message: string, variant: Toast['variant'] = 'success') {
  toasts.value = [...toasts.value.slice(-4), { id: ++nextId, message, variant }]
}
export function dismissToast(id: number) {
  toasts.value = toasts.value.filter((toast) => toast.id !== id)
}
