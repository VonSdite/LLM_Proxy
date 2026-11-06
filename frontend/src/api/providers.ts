import { api, download, type Query } from './client'

export interface Provider {
  name: string
  enabled: boolean
  api: string
  source_format: 'openai_chat' | 'openai_responses' | 'claude_chat'
  api_key: string | null
  auth_group: string | null
  proxy_mode: string
  proxy: string | null
  timeout_seconds: number | null
  max_retries: number | null
  verify_ssl: boolean
  force_upstream_stream: boolean
  safe_desensitization_enabled: boolean
  model_list: string[]
  hidden_model_list: string[]
  hook: string | null
}
export interface AuthEntry {
  id: string
  enabled: boolean
  headers: Record<string, string>
  max_concurrency?: number | null
  cooldown_seconds_on_429?: number | null
  request_quota_per_minute?: number | null
  request_quota_per_day?: number | null
  token_quota_per_minute?: number | null
  token_quota_per_day?: number | null
}
export interface AuthGroupSummary {
  entry_count: number
  available_count: number
  cooldown_count: number
  disabled_count: number
}
export interface AuthGroup {
  name: string
  strategy: 'least_inflight' | 'sticky_failover'
  cooldown_seconds_on_429: number | null
  entries: AuthEntry[]
  summary?: AuthGroupSummary
  provider_count?: number
}
export interface AuthRuntimeEntry extends Omit<AuthEntry, 'headers'> {
  status: string
  disabled: boolean
  disabled_reason: string | null
  inflight: number
  cooldown_until: string | null
  minute_request_count: number
  day_request_count: number
  minute_total_tokens: number
  day_total_tokens: number
  last_status_code: number | null
  last_error_type: string | null
  last_error_message: string | null
  updated_at: string | null
}
export interface AuthRuntime {
  name: string
  summary: AuthGroupSummary
  entries: AuthRuntimeEntry[]
}
export interface ModelTestResult {
  requested_model: string
  available: boolean | null
  first_token_latency_ms: number | null
  tps: number | null
  response_model: string | null
  error: string | null
}
export type ModelTestInput = Provider & { auth_entry_id: string; models: string[] }
const providerPath = (name: string) => `/api/providers/${encodeURIComponent(name)}`
const groupPath = (name: string) => `/api/auth-groups/${encodeURIComponent(name)}`
export const providersApi = {
  list: (signal?: AbortSignal) => api<Provider[]>('/api/providers', { signal }),
  save: (body: Provider, name?: string, signal?: AbortSignal) =>
    api<Provider>(name ? providerPath(name) : '/api/providers', {
      method: name ? 'PUT' : 'POST',
      body,
      signal,
    }),
  remove: (name: string, signal?: AbortSignal) =>
    api<void>(providerPath(name), { method: 'DELETE', signal }),
  copy: (name: string, signal?: AbortSignal) =>
    api<Provider>(`${providerPath(name)}/copy`, { method: 'POST', signal }),
  enabled: (name: string, enabled: boolean, signal?: AbortSignal) =>
    api<Provider>(`${providerPath(name)}/${enabled ? 'enable' : 'disable'}`, {
      method: 'POST',
      signal,
    }),
  order: (names: string[], signal?: AbortSignal) =>
    api<void>('/api/providers/order', { method: 'PUT', body: { names }, signal }),
  batch: (names: string[], action: 'enable' | 'disable' | 'delete', signal?: AbortSignal) =>
    api<{ count: number }>('/api/providers/batch', {
      method: 'POST',
      body: { names, action },
      signal,
    }),
  export: (names: string[], signal?: AbortSignal) =>
    download('/api/providers/export', 'llm-proxy-providers.json', {
      method: 'POST',
      body: { names },
      signal,
    }),
  import: (body: unknown, signal?: AbortSignal) =>
    api<{ count: number; auth_groups_count: number }>('/api/providers/import', {
      method: 'POST',
      body,
      signal,
    }),
  fetchModels: (query: Query, signal?: AbortSignal) =>
    api<{ fetched_models: string[] }>('/api/providers/fetch-models', { query, signal }),
  test: (body: ModelTestInput, signal?: AbortSignal) =>
    api<{ results: ModelTestResult[] }>('/api/providers/test-models', {
      method: 'POST',
      body,
      signal,
    }),
}
export const authGroupsApi = {
  list: (signal?: AbortSignal) => api<AuthGroup[]>('/api/auth-groups', { signal }),
  get: (name: string, signal?: AbortSignal) => api<AuthGroup>(groupPath(name), { signal }),
  save: (body: AuthGroup, name?: string, signal?: AbortSignal) =>
    api<AuthGroup>(name ? groupPath(name) : '/api/auth-groups', {
      method: name ? 'PUT' : 'POST',
      body,
      signal,
    }),
  remove: (name: string, signal?: AbortSignal) =>
    api<void>(groupPath(name), { method: 'DELETE', signal }),
  importEntries: (yaml: string, signal?: AbortSignal) =>
    api<{ entries: AuthEntry[] }>('/api/auth-groups/import-entries', {
      method: 'POST',
      body: { yaml },
      signal,
    }),
  runtime: (name: string, signal?: AbortSignal) =>
    api<AuthRuntime>(`${groupPath(name)}/runtime`, { signal }),
  entryAction: (
    name: string,
    id: string,
    action: 'enable' | 'disable' | 'clear-cooldown' | 'reset-minute-usage' | 'reset' | 'restore',
    signal?: AbortSignal,
  ) =>
    api<void>(`${groupPath(name)}/entries/${encodeURIComponent(id)}/${action}`, {
      method: 'POST',
      signal,
    }),
}
