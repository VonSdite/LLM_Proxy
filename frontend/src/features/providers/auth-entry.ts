import type { AuthEntry } from '@/api/providers'

export const entryLimits = [
  {
    key: 'max_concurrency',
    label: '最大并发',
    help: [
      '作用：限制该条目同时进行的上游请求数，流式请求结束后释放占用。',
      '达到上限：暂不选择此条目。',
      '留空：不限制并发数。',
    ],
  },
  {
    key: 'cooldown_seconds_on_429',
    label: '429 冷却（秒）',
    help: [
      '作用：该条目收到 429 时的冷却时长，单位为秒。',
      '上游返回有效 Retry-After 时，优先使用上游值。',
      '留空：继承认证组默认值。',
    ],
  },
  {
    key: 'request_quota_per_minute',
    label: '请求 / 分钟',
    help: [
      '计数：服务本地时间每个自然分钟内已发出的上游请求数，包含失败尝试。',
      '达到上限：暂停选择此条目，下一分钟重新计数。',
      '留空：不限制每分钟请求数。',
    ],
  },
  {
    key: 'request_quota_per_day',
    label: '请求 / 天',
    help: [
      '计数：服务本地时间每个自然日内已发出的上游请求数，包含失败尝试。',
      '达到上限：暂停选择此条目，下一日重新计数。',
      '留空：不限制每天请求数。',
    ],
  },
] as const
export type LimitKey = (typeof entryLimits)[number]['key']
export interface EntryDraft extends Omit<AuthEntry, 'headers'> {
  key: number
  headers: { key: number; name: string; value: string }[]
}
let sequence = 0
export function entryDraft(entry?: AuthEntry): EntryDraft {
  const value = entry || { id: '', enabled: true, headers: { Authorization: 'Bearer ' } }
  return {
    ...value,
    key: ++sequence,
    headers: Object.entries(value.headers).map(([name, value]) => ({
      key: ++sequence,
      name,
      value,
    })),
  }
}
export function entryPayload(entry: EntryDraft): AuthEntry {
  return {
    id: entry.id.trim(),
    enabled: entry.enabled,
    headers: Object.fromEntries(
      entry.headers
        .filter((header) => header.name.trim())
        .map((header) => [header.name.trim(), header.value.trim()]),
    ),
    ...Object.fromEntries(entryLimits.map(({ key }) => [key, entry[key] ?? null])),
  }
}
export function entryErrors(entry: EntryDraft, others: EntryDraft[]): Record<string, string> {
  const errors: Record<string, string> = {}
  if (!entry.id.trim()) errors.id = '请输入 Entry ID'
  else if (others.some((other) => other.key !== entry.key && other.id.trim() === entry.id.trim()))
    errors.id = 'Entry ID 重复'
  const headers = entry.headers.filter((header) => header.name.trim())
  if (!headers.length) errors.headers = '至少配置一个 Header'
  const used = new Set<string>()
  for (const header of headers) {
    const name = header.name.trim()
    if (!/^[!#$%&'*+.^_`|~\w-]+$/.test(name)) errors.headers = 'Header 名称格式不正确'
    if (used.has(name.toLowerCase())) errors.headers = 'Header 名称重复'
    used.add(name.toLowerCase())
  }
  for (const { key } of entryLimits)
    if (entry[key] != null && (!Number.isInteger(entry[key]) || entry[key]! < 1))
      errors[key] = '请输入正整数，留空表示继承或不限额'
  return errors
}
export function entriesYaml(entries: AuthEntry[]) {
  const lines = ['entries:']
  for (const entry of entries) {
    lines.push(
      `  - id: ${JSON.stringify(entry.id)}`,
      `    enabled: ${entry.enabled}`,
      '    headers:',
    )
    for (const [name, value] of Object.entries(entry.headers))
      lines.push(`      ${JSON.stringify(name)}: ${JSON.stringify(value)}`)
    if (!Object.keys(entry.headers).length) lines[lines.length - 1] = '    headers: {}'
    for (const { key } of entryLimits)
      if (entry[key] != null) lines.push(`    ${key}: ${entry[key]}`)
  }
  return `${lines.join('\n')}\n`
}
