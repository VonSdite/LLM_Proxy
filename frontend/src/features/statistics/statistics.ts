import type { StatisticsFilters, StatisticsView, UsageRow } from '@/api/statistics'
import type { TableColumn } from '@/components/ui'
import { number } from '@/features/shared/format'

export function dateInput(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
export function datePreset(days: number, end = new Date()): StatisticsFilters {
  const start = new Date(end)
  start.setDate(end.getDate() - days + 1)
  return { start_date: dateInput(start), end_date: dateInput(end), username: [], request_model: [] }
}
function parseDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null
  const [year, month, day] = value.split('-').map(Number) as [number, number, number]
  const date = new Date(year, month - 1, day)
  return dateInput(date) === value ? date : null
}
export function dateErrors(filters: StatisticsFilters) {
  const errors: { start?: string; end?: string } = {}
  const start = parseDate(filters.start_date)
  const end = parseDate(filters.end_date)
  if (!start) errors.start = '请选择开始日期'
  if (!end) errors.end = '请选择结束日期'
  if (start && end) {
    const limit = new Date(start.getFullYear() + 1, start.getMonth(), start.getDate())
    if (limit.getMonth() !== start.getMonth()) limit.setDate(0)
    if (end < start) errors.end = '结束日期不能早于开始日期'
    else if (end > limit) errors.end = '查询范围最多一年'
  }
  return errors
}
export function cacheRate(value: number | null, status = 'known') {
  return status === 'unknown' || value === null
    ? '未知'
    : `${(Math.max(0, value) * 100).toFixed(2)}%`
}
export function duration(row: UsageRow) {
  if (!row.start_time || !row.end_time) return '—'
  const elapsed =
    new Date(row.end_time.replace(' ', 'T')).getTime() -
    new Date(row.start_time.replace(' ', 'T')).getTime()
  return Number.isFinite(elapsed) ? (Math.max(0, elapsed) / 1000).toFixed(2) : '—'
}
export const tokenKeys = [
  'prompt_tokens',
  'completion_tokens',
  'total_tokens',
  'cache_read_input_tokens',
] as const
const tokens: TableColumn[] = [
  { key: 'prompt_tokens', label: '输入 Token', width: 132, sortable: true, align: 'right' },
  { key: 'completion_tokens', label: '输出 Token', width: 132, sortable: true, align: 'right' },
  { key: 'total_tokens', label: '总 Token', width: 140, sortable: true, align: 'right' },
  {
    key: 'cache_read_input_tokens',
    label: '缓存读取 Token',
    help: '上游报告的缓存命中输入 Token，是输入 Token 的一部分，不应再加到总 Token。没有缓存明细时，不能把显示的 0 直接理解为没有缓存命中。',
    width: 180,
    sortable: true,
    align: 'right',
  },
  {
    key: 'cache_hit_rate',
    label: '缓存命中率',
    width: 154,
    sortable: true,
    align: 'right',
    help: '缓存读取 Token ÷ 已提供缓存明细请求的输入 Token。汇总按 Token 加权，不是各行百分比平均值；未知表示上游未提供可计算的明细，部分明细时只统计已知输入。',
  },
]
const user: TableColumn = { key: 'username', label: '用户名', width: 144, sortable: true }
const ip: TableColumn = { key: 'ip_address', label: 'IP', width: 140, sortable: true }
const flow: TableColumn = {
  key: 'model_flow',
  label: '模型流向',
  width: 280,
  sortable: true,
  help: '依次展示客户端请求的模型 ID → 映射实际选择的目标（如有）→ 上游响应报告的模型名。三者可以不同，缺失的阶段不显示。',
}
const count: TableColumn = {
  key: 'request_count',
  label: '请求数',
  width: 110,
  sortable: true,
  align: 'right',
}
export const columnsByView: Record<StatisticsView, TableColumn[]> = {
  stats: [ip, user, flow, ...tokens, count],
  user_usage: [
    user,
    ...tokens,
    count,
    { key: 'ip_count', label: 'IP 数', width: 100, sortable: true, align: 'right' },
    { key: 'last_request_date', label: '最近请求日期', width: 160, sortable: true },
  ],
  api_key_usage: [
    { key: 'key_name', label: 'Key 名称', width: 180, sortable: true },
    { key: 'key_preview', label: 'Key', width: 190, sortable: true },
    ...tokens,
    count,
  ],
  logs: [
    ip,
    user,
    flow,
    ...tokens,
    { key: 'start_time', label: '开始时间', width: 190, sortable: true },
    { key: 'end_time', label: '结束时间', width: 190, sortable: true },
    {
      key: 'duration',
      label: '耗时（秒）',
      width: 150,
      sortable: true,
      align: 'right',
      help: '从收到请求到响应结束的总时长，流式请求包含全部输出时间，不是首 Token 延迟。缺少起止时间时显示 —。',
    },
  ],
}
export function rowKey(row: UsageRow, view: StatisticsView) {
  if (view === 'logs') return String(row.id)
  if (view === 'api_key_usage') return String(row.api_key_id)
  if (view === 'user_usage') return row.username || ''
  return JSON.stringify([
    row.ip_address,
    row.username,
    row.request_model,
    row.target_model_id,
    row.response_model,
  ])
}
export function totals(rows: UsageRow[], view: StatisticsView) {
  if (view === 'logs') return undefined
  const sum = (key: keyof UsageRow) =>
    rows.reduce((value, row) => value + (typeof row[key] === 'number' ? Number(row[key]) : 0), 0)
  const knownPrompt = sum('cache_known_prompt_tokens')
  const firstKey = columnsByView[view][0]!.key
  return {
    ...Object.fromEntries(
      [...tokenKeys, 'request_count'].map((key) => [key, number(sum(key as keyof UsageRow))]),
    ),
    [firstKey]: '合计',
    cache_hit_rate: cacheRate(
      knownPrompt > 0 ? sum('cache_read_input_tokens') / knownPrompt : null,
    ),
  }
}
