import { api } from './client'
import type { ListQuery, ModelPermissions, PagedResult, UsageTotals } from './permissions'
export interface ApiKey extends UsageTotals {
  id: number
  name: string
  api_key: string
  key_prefix: string
  key_suffix: string
  enabled: boolean
  model_permissions: ModelPermissions
  allowed_models_count: number
  available_models_count: number
  token_limit_k: number | null
  token_limit_remaining: number | null
  created_at: string
  last_used_at: string | null
}
export interface ApiKeyInput {
  name: string
  enabled: boolean
  model_permissions: ModelPermissions
  token_limit_k: number | null
}
export const apiKeysApi = {
  async list(query: ListQuery, signal?: AbortSignal): Promise<PagedResult<ApiKey>> {
    const result = await api<{ api_keys: ApiKey[]; total: number; available_models: string[] }>(
      '/api/api-keys',
      { query: { ...query }, signal },
    )
    return { items: result.api_keys, total: result.total, models: result.available_models }
  },
  create: (body: ApiKeyInput, signal?: AbortSignal) =>
    api<{ api_key: ApiKey }>('/api/api-keys', { method: 'POST', body, signal }),
  update: (id: number, body: Partial<ApiKeyInput>, signal?: AbortSignal) =>
    api<void>(`/api/api-keys/${id}`, { method: 'PUT', body, signal }),
  remove: (id: number, signal?: AbortSignal) =>
    api<void>(`/api/api-keys/${id}`, { method: 'DELETE', signal }),
  toggle: (id: number, signal?: AbortSignal) =>
    api<void>(`/api/api-keys/${id}/toggle`, { method: 'POST', signal }),
}
