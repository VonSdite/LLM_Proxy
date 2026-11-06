import { api, download, type Query } from './client'

export type StatisticsView = 'stats' | 'user_usage' | 'api_key_usage' | 'logs'
export interface StatisticsFilters {
  start_date: string
  end_date: string
  username: string[]
  request_model: string[]
}
export interface UsageRow {
  id?: number
  api_key_id?: number
  username?: string
  ip_address?: string
  key_name?: string
  key_preview?: string
  status?: 'enabled' | 'disabled' | 'deleted'
  request_model?: string
  target_model_id?: string | null
  response_model?: string | null
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cache_read_input_tokens: number
  cache_known_prompt_tokens?: number
  cache_usage_status: 'known' | 'partial' | 'unknown'
  cache_hit_rate: number | null
  request_count?: number
  ip_count?: number
  last_request_date?: string
  start_time?: string
  end_time?: string
}
export interface StatisticsResult {
  rows: UsageRow[]
  total: number
}
export interface StatisticsImportResult {
  request_logs_inserted_count: number
  request_logs_skipped_count: number
  daily_request_stats_inserted_count: number
  daily_request_stats_merged_count: number
}
const paths: Record<StatisticsView, string> = {
  stats: '/api/statistics',
  user_usage: '/api/statistics/user-usage-summary',
  api_key_usage: '/api/statistics/api-key-usage-summary',
  logs: '/api/request-logs',
}
export const statisticsApi = {
  async list(view: StatisticsView, query: Query, signal: AbortSignal): Promise<StatisticsResult> {
    if (view === 'logs') {
      const result = await api<{ logs: UsageRow[]; total: number }>(paths.logs, { query, signal })
      return { rows: result.logs, total: result.total }
    }
    const rows = await api<UsageRow[]>(paths[view], { query, signal })
    return { rows, total: rows.length }
  },
  async candidates(signal: AbortSignal) {
    const [users, models] = await Promise.all([
      api<string[]>('/api/usernames', { signal }),
      api<string[]>('/api/request-models', { signal }),
    ])
    return { users, models }
  },
  exportExcel: (view: StatisticsView, query: Query, signal: AbortSignal) =>
    download('/api/statistics/export', 'statistics.xlsx', {
      query: { ...query, page: undefined, page_size: undefined, tab: view },
      signal,
    }),
  exportData: (query: Query, signal: AbortSignal) =>
    download('/api/statistics/daily-stats/export', 'statistics-data.json', {
      query: { ...query, page: undefined, page_size: undefined },
      signal,
    }),
  importData: (body: unknown, signal: AbortSignal) =>
    api<StatisticsImportResult>('/api/statistics/daily-stats/import', {
      method: 'POST',
      body,
      signal,
    }),
}
