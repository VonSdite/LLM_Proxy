import { api, download, type Query } from './client'

export type StatisticsInterval = '5m' | '1h' | '1d'

export type StatisticsView =
  | 'stats'
  | 'model_usage'
  | 'response_model_usage'
  | 'user_usage'
  | 'api_key_usage'
  | 'ip_usage'
  | 'models'
  | 'logs'
export interface StatisticsFilters {
  start_date: string
  end_date: string
  username: string[]
  request_model: string[]
  target_model_id: string[]
  response_model: string[]
  ip_address: string[]
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
  usage_status?: 'known' | 'partial' | 'unknown'
  cache_read_input_tokens: number
  cache_known_prompt_tokens?: number
  cache_usage_status: 'known' | 'partial' | 'unknown'
  cache_hit_rate: number | null
  request_count?: number
  ip_count?: number
  last_request_date?: string
  start_time?: string
  end_time?: string
  outcome?: 'success' | 'error' | 'cancelled' | null
  http_status?: number | null
  error_type?: string | null
  is_stream?: boolean | number | null
  duration_ms?: number | null
  first_token_latency_ms?: number | null
  tps?: number | null
  tps_unavailable_reason?: string | null
  success_rate?: number | null
  upstream_success_rate?: number | null
  upstream_success_count?: number
  upstream_error_count?: number
  upstream_exception_count?: number
  upstream_cancelled_count?: number
  upstream_unknown_count?: number
  success_count?: number
  error_count?: number
  cancelled_count?: number
  unknown_count?: number
  recorded_count?: number
  cache_sample_count?: number
  cache_hit_count?: number
  cache_request_hit_rate?: number | null
  latency_sample_count?: number
  tps_sample_count?: number
}
export interface StatisticsOverview {
  totals: UsageRow
  series: (UsageRow & { date: string })[]
  series_interval?: StatisticsInterval
  series_partial?: boolean
  models: UsageRow[]
  model_flows: UsageRow[]
  response_models: UsageRow[]
  ips: UsageRow[]
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
  ip_usage: '/api/statistics/overview',
  model_usage: '/api/statistics/overview',
  response_model_usage: '/api/statistics/overview',
  models: '/api/statistics/overview',
  logs: '/api/request-logs',
}
export const statisticsApi = {
  overview: (query: Query, signal: AbortSignal) =>
    api<StatisticsOverview>('/api/statistics/overview', { query, signal }),
  async list(view: StatisticsView, query: Query, signal: AbortSignal): Promise<StatisticsResult> {
    if (
      view === 'models' ||
      view === 'model_usage' ||
      view === 'response_model_usage' ||
      view === 'ip_usage'
    ) {
      const overview = await statisticsApi.overview(query, signal)
      const rows =
        view === 'response_model_usage'
          ? overview.response_models
          : view === 'ip_usage'
            ? overview.ips
            : view === 'model_usage'
              ? overview.model_flows
              : overview.models
      return { rows, total: rows.length }
    }
    if (view === 'logs') {
      const result = await api<{ logs: UsageRow[]; total: number }>(paths.logs, { query, signal })
      return { rows: result.logs, total: result.total }
    }
    const rows = await api<UsageRow[]>(paths[view], { query, signal })
    return { rows, total: rows.length }
  },
  async candidates(signal: AbortSignal) {
    return api<{
      users: string[]
      models: string[]
      target_models: string[]
      response_models: string[]
      ip_addresses: string[]
    }>('/api/statistics/filter-options', { signal })
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
