import type { TableColumn } from './types'

/** 按可用空间分配列宽；手动宽度优先，其余列在可读下限与上限之间伸缩。 */
export function fitColumnWidths(
  columns: TableColumn[],
  saved: Record<string, number>,
  available: number,
) {
  const entries = columns.map((column) => {
    const min = column.minWidth ?? 80
    const max = column.maxWidth ?? Infinity
    return {
      key: column.key,
      min,
      max,
      width: Math.max(min, Math.min(max, saved[column.key] ?? column.width ?? 180)),
      weight: column.grow ?? 1,
      flexible: !column.fixed && saved[column.key] === undefined,
    }
  })
  let remaining =
    available > 0 ? available - entries.reduce((sum, entry) => sum + entry.width, 0) : 0
  let active = entries.filter((entry) => entry.flexible && entry.weight > 0)
  while (active.length && Math.abs(remaining) > 0.1) {
    const weight = active.reduce((sum, entry) => sum + entry.weight, 0)
    let consumed = 0
    for (const entry of active) {
      const next = Math.max(
        entry.min,
        Math.min(entry.max, entry.width + (remaining * entry.weight) / weight),
      )
      consumed += next - entry.width
      entry.width = next
    }
    if (Math.abs(consumed) < 0.1) break
    remaining -= consumed
    active = active.filter((entry) =>
      remaining > 0 ? entry.width < entry.max : entry.width > entry.min,
    )
  }
  return Object.fromEntries(entries.map((entry) => [entry.key, entry.width]))
}
