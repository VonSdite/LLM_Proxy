export function number(value: number | null | undefined) {
  return value == null ? '—' : value.toLocaleString()
}
export function tokenK(value: number | null | undefined) {
  return value == null
    ? '—'
    : (value / 1000).toLocaleString(undefined, { maximumFractionDigits: 2 })
}
export function dateTime(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}
export async function copyText(value: string) {
  if (navigator.clipboard) {
    await navigator.clipboard.writeText(value)
    return
  }
  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.append(textarea)
  const focused = document.activeElement as HTMLElement | null
  textarea.select()
  const copied = document.execCommand('copy')
  textarea.remove()
  focused?.focus()
  if (!copied) throw new Error('复制失败，请手动选择内容复制')
}
