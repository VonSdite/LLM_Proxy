import type { StatisticsFilters, StatisticsView, UsageRow } from '@/api/statistics'
import type { TableColumn } from '@/components/ui'
import { number } from '@/features/shared/format'

export function dateInput(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
export function datePreset(days: number, end = new Date()): StatisticsFilters {
  const start = new Date(end)
  start.setDate(end.getDate() - days + 1)
  return {
    start_date: dateInput(start),
    end_date: dateInput(end),
    username: [],
    request_model: [],
    target_model_id: [],
    response_model: [],
    ip_address: [],
  }
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
  if (row.duration_ms != null) return (row.duration_ms / 1000).toFixed(2)
  if (!row.start_time || !row.end_time) return '—'
  const elapsed =
    new Date(row.end_time.replace(' ', 'T')).getTime() -
    new Date(row.start_time.replace(' ', 'T')).getTime()
  return Number.isFinite(elapsed) ? (Math.max(0, elapsed) / 1000).toFixed(2) : '—'
}
export function requestTime(value?: string) {
  return value ? value.replace(/\.\d+/, '').replace('T', ' ') : '—'
}
export const successHelp = [
  '上游成功率 = 上游成功次数 ÷（上游成功次数 + 上游失败次数）。每次实际调用分别统计；第一次失败、重试成功，计成功 1 次、失败 1 次，成功率 50%。',
  '失败指上游明确返回的 HTTP 错误或响应中的错误事件。连接失败、超时、连接中断归为异常；代理内部错误不计上游失败。异常、中止和未知不进入分母。',
  '请求数仍表示客户端请求次数。成功率按当前筛选及行分组计算，合计按调用次数加权；无成功或失败样本时显示 —。',
  '未知表示未采集调用明细的请求数，不从最终结果推断重试次数；模型测试记录未采集此明细时同样不参与计算。',
]
export function requestError(row: UsageRow) {
  const names: Record<string, string> = {
    upstream_error: '上游错误',
    stream_error: '响应中断',
    response_error: '响应错误',
    timeout: '请求超时',
  }
  return names[row.error_type || ''] || row.error_type || ''
}
export const outcomeHelp = [
  '本列表示客户端请求的最终结果；上游成功率按每次实际上游调用单独计算，代理内部错误不计上游失败。',
  '成功：本次请求正常结束，未检测到错误。',
  '失败：上游返回错误，或连接、响应传输发生异常；下方显示已记录的错误信息。',
  '已取消：响应完成前连接被关闭，例如客户端停止生成或断开连接。',
  '未知：这条日志没有记录请求结果；有 Token 用量，也不代表当时保存了成功状态。',
  '发生重试时显示最终结果，不把每次尝试分别算作一条请求。',
]
export const latencyHelp = [
  '从代理收到请求到发出首个非空正文、思考或工具参数片段，包含代理处理和上游重试。角色、心跳和使用量事件不计入。',
  '概览为成功请求中可测样本的平均值。非流式、没有可识别内容片段和历史未采集的请求显示 —。',
]
export const tpsHelp = [
  '平均输出速度（Token/s）＝上游报告的输出 Token ÷ 请求总耗时（秒），包含首字等待、思考、代理处理及重试。',
  '输出 Token 已包含的推理 Token 不重复相加；隐藏思考、单数据块和非流式响应均使用同一公式。',
  '概览为成功请求中有效 TPS 的平均值，不是整个服务每秒处理的 Token 总量，也不是模型纯解码速度。',
  '历史明细按已记录的输出用量和耗时采用相同公式；缺少有效数据、失败或取消时显示 —，可悬浮查看原因。',
]
export function tpsUnavailableHelp(row: UsageRow) {
  const reasons: Record<string, string> = {
    missing_usage: '上游未提供可用的输出 Token 用量，无法计算 TPS。',
    no_output: '没有正数的输出 Token 用量，无法计算 TPS。',
    invalid_duration: '缺少有效的请求总耗时，无法计算 TPS。',
    unsuccessful: '请求失败或被取消，不计算完整响应的 TPS。',
  }
  if (row.tps_unavailable_reason && reasons[row.tps_unavailable_reason])
    return reasons[row.tps_unavailable_reason]!
  if (row.outcome === 'error' || row.outcome === 'cancelled') return reasons.unsuccessful!
  return '缺少有效的输出用量或总耗时，无法计算 TPS。'
}
export const cacheHelp = [
  '缓存读取 Token ÷ 已报告缓存明细的输入 Token，按 Token 加权，不是各请求百分比的平均值。',
  '未知缓存请求不进入分母。请求命中率另表示有缓存读取的请求占已知缓存请求的比例，二者含义不同。',
]
export function metric(value: number | null | undefined, digits = 1) {
  return value == null || !Number.isFinite(value)
    ? '—'
    : value.toLocaleString(undefined, { maximumFractionDigits: digits })
}
export function percent(value: number | null | undefined) {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`
}
export function compact(value: number) {
  return value >= 1e6
    ? `${metric(value / 1e6)}M`
    : value >= 1e3
      ? `${metric(value / 1e3)}k`
      : metric(value, 0)
}
export const tokenKeys = [
  'prompt_tokens',
  'completion_tokens',
  'total_tokens',
  'cache_read_input_tokens',
] as const
const tokens: TableColumn[] = [
  {
    key: 'prompt_tokens',
    label: '输入Tok',
    width: 76,
    minWidth: 76,
    grow: 0,
    sortable: true,
    align: 'right',
  },
  {
    key: 'completion_tokens',
    label: '输出Tok',
    width: 76,
    minWidth: 76,
    grow: 0,
    sortable: true,
    align: 'right',
  },
  {
    key: 'total_tokens',
    label: '总Tok',
    width: 76,
    minWidth: 76,
    grow: 0,
    sortable: true,
    align: 'right',
  },
  {
    key: 'cache_read_input_tokens',
    label: '缓存Tok',
    help: '上游报告的缓存命中输入 Token，是输入 Token 的一部分，不应再加到总 Token。没有缓存明细时，不能把显示的 0 直接理解为没有缓存命中。',
    width: 96,
    minWidth: 84,
    grow: 0,
    sortable: true,
    align: 'right',
  },
  {
    key: 'cache_hit_rate',
    label: '缓存命中率',
    width: 112,
    minWidth: 92,
    grow: 0,
    sortable: true,
    align: 'right',
    help: '缓存读取 Token ÷ 已提供缓存明细请求的输入 Token。汇总按 Token 加权，不是各行百分比平均值；未知表示上游未提供可计算的明细，部分明细时只统计已知输入。',
  },
]
const user: TableColumn = {
  key: 'username',
  label: '用户名',
  width: 72,
  minWidth: 72,
  grow: 0,
  sortable: true,
}
const ip: TableColumn = {
  key: 'ip_address',
  label: 'IP',
  minWidth: 0,
  fitContent: true,
  grow: 0,
  sortable: true,
}
const requestModel: TableColumn = {
  key: 'request_model',
  label: '请求模型',
  width: 200,
  minWidth: 0,
  fitContent: true,
  maxWidth: 640,
  grow: 2,
  sortable: true,
  help: '客户端请求的原始模型 ID，包含模型映射名称。点击请求 ID 可筛选。',
}
const targetModel: TableColumn = {
  key: 'target_model_id',
  label: '映射模型',
  width: 220,
  minWidth: 0,
  fitContent: true,
  maxWidth: 640,
  grow: 2,
  sortable: true,
  help: '模型映射选中的实际目标模型 ID，与请求 ID 相同时也显示；没有映射目标时显示 -。',
}
const responseModel: TableColumn = {
  key: 'response_model',
  label: '响应模型',
  width: 200,
  minWidth: 0,
  fitContent: true,
  maxWidth: 640,
  grow: 2,
  sortable: true,
  help: '上游响应实际报告的模型 ID，不从请求模型或映射目标推测；未提供时显示未知。',
}
const count: TableColumn = {
  key: 'request_count',
  label: '请求数',
  width: 80,
  minWidth: 72,
  grow: 0,
  sortable: true,
  align: 'right',
}
const latency: TableColumn = {
  key: 'first_token_latency_ms',
  label: '首字延迟',
  width: 96,
  minWidth: 80,
  grow: 0,
  sortable: true,
  align: 'right',
  help: latencyHelp,
}
const tps: TableColumn = {
  key: 'tps',
  label: 'TPS',
  width: 96,
  minWidth: 96,
  grow: 0,
  sortable: true,
  align: 'right',
  help: tpsHelp,
}
const success: TableColumn = {
  key: 'upstream_success_rate',
  label: '上游成功率',
  width: 116,
  minWidth: 100,
  grow: 0,
  align: 'right',
  sortable: true,
  help: successHelp,
}
export const columnsByView: Record<StatisticsView, TableColumn[]> = {
  model_usage: [requestModel, targetModel, responseModel, count, success, ...tokens],
  response_model_usage: [{ ...responseModel, maxWidth: undefined }, count, success, ...tokens],
  models: [
    {
      key: 'request_model',
      label: '请求模型',
      width: 280,
      minWidth: 0,
      fitContent: true,
      maxWidth: 640,
      grow: 2,
      sortable: true,
    },
    count,
    success,
    {
      key: 'cache_hit_rate',
      label: '缓存命中率',
      width: 112,
      minWidth: 92,
      grow: 0,
      align: 'right',
      sortable: true,
      help: cacheHelp,
    },
    latency,
    tps,
    {
      key: 'total_tokens',
      label: '总Tok',
      width: 76,
      minWidth: 76,
      grow: 0,
      align: 'right',
      sortable: true,
    },
  ],
  ip_usage: [ip, user, count, success, ...tokens],
  stats: [ip, user, requestModel, targetModel, responseModel, count, success, ...tokens],
  user_usage: [
    user,
    count,
    success,
    ...tokens,
    {
      key: 'ip_count',
      label: 'IP 数',
      width: 72,
      minWidth: 64,
      grow: 0,
      sortable: true,
      align: 'right',
    },
    {
      key: 'last_request_date',
      label: '最近请求日期',
      width: 128,
      minWidth: 128,
      fixed: true,
      sortable: true,
    },
  ],
  api_key_usage: [
    { key: 'key_name', label: 'Key 名称', width: 180, minWidth: 112, grow: 2, sortable: true },
    {
      key: 'key_preview',
      label: 'Key',
      width: 176,
      minWidth: 140,
      maxWidth: 360,
      grow: 1,
      sortable: true,
    },
    count,
    success,
    ...tokens,
  ],
  logs: [
    {
      key: 'start_time',
      label: '请求时间',
      width: 144,
      minWidth: 144,
      maxWidth: 240,
      fixed: true,
      sortable: true,
    },
    user,
    ip,
    { ...requestModel, width: 152, maxWidth: 400, grow: 1 },
    { ...targetModel, width: 184, maxWidth: 560, grow: 2 },
    { ...responseModel, width: 152, maxWidth: 400, grow: 1 },
    {
      key: 'outcome',
      label: '结果',
      width: 80,
      minWidth: 80,
      maxWidth: 160,
      fixed: true,
      sortable: true,
      help: outcomeHelp,
    },
    {
      key: 'prompt_tokens',
      label: '输入Tok',
      width: 76,
      minWidth: 76,
      grow: 0,
      maxWidth: 150,
      sortable: true,
      align: 'right',
    },
    {
      key: 'completion_tokens',
      label: '输出Tok',
      width: 76,
      minWidth: 76,
      grow: 0,
      maxWidth: 150,
      sortable: true,
      align: 'right',
    },
    {
      key: 'cache_read_input_tokens',
      label: '缓存Tok',
      width: 96,
      minWidth: 84,
      grow: 0,
      maxWidth: 180,
      sortable: true,
      align: 'right',
      help: [
        '数值是缓存读取 Token，已包含在输入 Token 中，不重复计入总量。',
        '上游未报告缓存明细时显示未知；明确报告为 0 时显示 0。',
      ],
    },
    {
      key: 'cache_hit_rate',
      label: '缓存命中率',
      width: 92,
      minWidth: 92,
      grow: 0,
      maxWidth: 154,
      sortable: true,
      align: 'right',
      help: [
        '缓存读取 Token ÷ 本次请求的输入 Token。缓存读取 Token 已包含在输入 Token 中。',
        '上游未报告缓存明细或输入 Token 为 0 时显示未知；明确报告缓存读取为 0 且输入大于 0 时显示 0.00%。',
      ],
    },
    {
      key: 'total_tokens',
      label: '总Tok',
      width: 76,
      minWidth: 76,
      grow: 0,
      maxWidth: 150,
      sortable: true,
      align: 'right',
    },
    { ...latency, width: 80, minWidth: 80, maxWidth: 180, grow: 0 },
    { ...tps, width: 96, minWidth: 96, maxWidth: 180, grow: 0 },
    {
      key: 'duration',
      label: '耗时',
      width: 80,
      minWidth: 80,
      grow: 0,
      maxWidth: 160,
      sortable: true,
      align: 'right',
      help: '从收到请求到响应结束的总时长，单位为秒。包含准备、重试和全部输出时间。缺少有效计时时显示 —。',
    },
  ],
}
export function statisticsColumns(rows: UsageRow[], view: StatisticsView): TableColumn[] {
  const footer: Record<string, string | number | undefined> | undefined = totals(rows, view)
  const columns = columnsByView[view]
  return columns.map((column) => {
    if (column.align !== 'right') return column.grow ? { ...column, maxWidth: undefined } : column
    const values = rows.map((row) => {
      if (column.key === 'cache_hit_rate')
        return cacheRate(row.cache_hit_rate, row.cache_usage_status)
      if (column.key === 'upstream_success_rate') return percent(row.upstream_success_rate)
      if (column.key === 'duration') return `${duration(row)} s`
      if (column.key === 'tps') return row.tps == null ? '—' : `${metric(row.tps)} tok/s`
      if (column.key === 'first_token_latency_ms')
        return row.first_token_latency_ms == null
          ? '—'
          : `${metric(row.first_token_latency_ms / 1000, 2)} s`
      return number(row[column.key as (typeof tokenKeys)[number] | 'request_count' | 'ip_count'])
    })
    const total = footer?.[column.key]
    if (total !== undefined) values.push(String(total))
    // 等宽数字按字符预留空间；精确数值与单位始终完整显示，不使用 k/M 缩写。
    const minWidth = values.reduce(
      (width, value) => Math.max(width, value.length * 8 + 16),
      column.minWidth ?? 80,
    )
    return {
      ...column,
      minWidth,
      width: Math.max(column.width ?? 80, minWidth),
      // 模型列达到内容宽度后，由未手动调整的数值列分配剩余空间，铺满容器。
      grow: 1,
      maxWidth: undefined,
    }
  })
}
export function rowKey(row: UsageRow, view: StatisticsView) {
  if (view === 'response_model_usage') return JSON.stringify(row.response_model || null)
  if (view === 'models') return row.request_model || ''
  if (view === 'model_usage')
    return JSON.stringify([
      row.request_model,
      row.target_model_id || null,
      row.response_model || null,
    ])
  if (view === 'ip_usage') return row.ip_address || ''
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
  if (view === 'logs' || view === 'models') return undefined
  const sum = (key: keyof UsageRow) =>
    rows.reduce((value, row) => value + (typeof row[key] === 'number' ? Number(row[key]) : 0), 0)
  const knownPrompt = sum('cache_known_prompt_tokens')
  const decided = sum('upstream_success_count') + sum('upstream_error_count')
  const firstKey = columnsByView[view][0]!.key
  return {
    ...Object.fromEntries(
      [...tokenKeys, 'request_count'].map((key) => [key, number(sum(key as keyof UsageRow))]),
    ),
    [firstKey]: '合计',
    upstream_success_rate: percent(decided > 0 ? sum('upstream_success_count') / decided : null),
    cache_hit_rate: cacheRate(
      knownPrompt > 0 ? sum('cache_read_input_tokens') / knownPrompt : null,
    ),
  }
}
