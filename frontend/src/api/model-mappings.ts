import { api, download } from './client'

export interface MappingTarget {
  model_id: string
  priority: number
  enabled: boolean
  available_model?: boolean
  status?: 'available' | 'disabled' | 'auto_disabled' | 'cooldown' | 'unavailable'
  current?: boolean
  cooldown_until?: string | null
  last_status_code?: number | null
  last_error_type?: string | null
  last_error_message?: string | null
}
export interface ModelMapping {
  id: string
  enabled: boolean
  strategy: 'highest_priority' | 'sticky_failover'
  cooldown_seconds_on_429: number
  safe_desensitization_enabled: boolean
  targets: MappingTarget[]
  effective?: boolean
  conflict_source?: string | null
  current_target_model_id?: string | null
}
const path = (id: string) => `/api/model-mappings/${encodeURIComponent(id)}`
export const mappingsApi = {
  list: (signal?: AbortSignal) =>
    api<{ enabled: boolean; model_mappings: ModelMapping[]; available_targets: string[] }>(
      '/api/model-mappings',
      { signal },
    ),
  save: (body: ModelMapping, originalId?: string, signal?: AbortSignal) =>
    api<ModelMapping>(originalId ? path(originalId) : '/api/model-mappings', {
      method: originalId ? 'PUT' : 'POST',
      body,
      signal,
    }),
  remove: (id: string, signal?: AbortSignal) => api<void>(path(id), { method: 'DELETE', signal }),
  copy: (id: string, signal?: AbortSignal) =>
    api<ModelMapping>(`${path(id)}/copy`, { method: 'POST', signal }),
  enabled: (id: string, enabled: boolean, signal?: AbortSignal) =>
    api<ModelMapping>(`${path(id)}/${enabled ? 'enable' : 'disable'}`, { method: 'POST', signal }),
  order: (ids: string[], signal?: AbortSignal) =>
    api<void>('/api/model-mappings/order', { method: 'PUT', body: { ids }, signal }),
  batchDelete: (ids: string[], signal?: AbortSignal) =>
    api<{ count: number }>('/api/model-mappings/batch', {
      method: 'POST',
      body: { mapping_ids: ids, action: 'delete' },
      signal,
    }),
  toggleTarget: (id: string, model_id: string, enabled: boolean, signal?: AbortSignal) =>
    api<ModelMapping>(`${path(id)}/targets/toggle`, {
      method: 'POST',
      body: { model_id, enabled },
      signal,
    }),
  export: (ids: string[], signal?: AbortSignal) =>
    download('/api/model-mappings/export', 'llm-proxy-model-mappings.json', {
      method: 'POST',
      body: { mapping_ids: ids },
      signal,
    }),
  import: (body: unknown, signal?: AbortSignal) =>
    api<{ count: number }>('/api/model-mappings/import', { method: 'POST', body, signal }),
}
