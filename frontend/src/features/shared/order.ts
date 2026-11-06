export interface RowMove {
  id: string
  targetId: string
  after: boolean
}

// 顺序接口接收完整列表，筛选列表的移动仍以完整顺序为准。
export function moveItem<T>(items: readonly T[], move: RowMove, key: (item: T) => string): T[] {
  const moving = items.find((item) => key(item) === move.id)
  if (!moving || move.id === move.targetId || !items.some((item) => key(item) === move.targetId))
    return [...items]
  const next = items.filter((item) => key(item) !== move.id)
  const index = next.findIndex((item) => key(item) === move.targetId)
  next.splice(index + (move.after ? 1 : 0), 0, moving)
  return next
}
