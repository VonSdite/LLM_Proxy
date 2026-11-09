import { api, download } from './client'
import type { ListQuery, ModelPermissions, PagedResult, UsageTotals } from './permissions'
export interface User extends UsageTotals {
  id: number
  username: string
  ip_address: string
  whitelist_access_enabled: boolean
  model_permissions: ModelPermissions
  allowed_models_count: number
  available_models_count: number
  created_at: string
  updated_at: string
}
export interface UserInput {
  username: string
  ip_address: string
  whitelist_access_enabled?: boolean
  model_permissions?: ModelPermissions
}
export const usersApi = {
  async list(query: ListQuery, signal?: AbortSignal): Promise<PagedResult<User>> {
    const result = await api<{ users: User[]; total: number; available_models: string[] }>(
      '/api/users',
      { query: { ...query }, signal },
    )
    return { items: result.users, total: result.total, models: result.available_models }
  },
  create: (body: UserInput, signal?: AbortSignal) =>
    api<{ id: number }>('/api/users', { method: 'POST', body, signal }),
  update: (id: number, body: Partial<UserInput>, signal?: AbortSignal) =>
    api<void>(`/api/users/${id}`, { method: 'PUT', body, signal }),
  remove: (id: number, signal?: AbortSignal) =>
    api<void>(`/api/users/${id}`, { method: 'DELETE', signal }),
  toggle: (id: number, signal?: AbortSignal) =>
    api<void>(`/api/users/${id}/toggle`, { method: 'POST', signal }),
  batch: (
    ids: number[],
    action: 'delete' | 'set_model_permissions',
    permissions?: ModelPermissions,
    signal?: AbortSignal,
  ) =>
    api<{ count: number }>('/api/users/batch', {
      method: 'POST',
      body: { user_ids: ids, action, model_permissions: permissions },
      signal,
    }),
  export: (ids: number[], signal?: AbortSignal) =>
    download('/api/users/export', 'llm-proxy-users.json', {
      method: 'POST',
      body: { user_ids: ids },
      signal,
    }),
  exportFiltered: (keyword: string, signal?: AbortSignal) =>
    download('/api/users/export', 'llm-proxy-users.json', {
      method: 'POST',
      body: { keyword },
      signal,
    }),
  import: (body: unknown, signal?: AbortSignal) =>
    api<{ count: number; created_count: number; updated_count: number }>('/api/users/import', {
      method: 'POST',
      body,
      signal,
    }),
  whitelist: (enabled: boolean, signal?: AbortSignal) =>
    api<{ enabled: boolean }>('/api/providers/chat-whitelist', {
      method: 'PUT',
      body: { enabled },
      signal,
    }),
}
