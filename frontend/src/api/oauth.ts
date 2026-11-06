import { api, download } from './client'
export interface OAuthModel {
  id: string
  built_in: boolean
}
export interface OAuthModels {
  models: OAuthModel[]
  default_model?: string
}
export interface QuotaWindow {
  label: string
  remaining_percent: number | null
  used_percent?: number | null
  reset_at: number | string | null
  limit_window_seconds?: number
}
export interface OAuthUsage {
  request_count: number
  total_tokens: number
  estimated_cost_usd: number | null
  estimated_full_cost_usd: number | null
  limit_window_seconds?: number
}
export interface Quota {
  windows: QuotaWindow[]
  plan_type?: string
  refreshed_at?: string
  skipped?: boolean
  reason?: string
  current_usage?: OAuthUsage
  previous_usage?: OAuthUsage | null
}
export interface ResetCard {
  id: string
  title?: string
  description?: string
  name?: string
  label?: string
  expires_at: number | string | null
}
export interface OAuthFile {
  name: string
  path: string
  email: string
  plan_type: string
  enabled: boolean
  disabled: boolean
  availability_status: string
  availability_status_message: string
  availability_retry_at: string | null
  usage_status_message: string
  expired: string
  quota: Quota | null
  quota_error: string
  quota_refreshed_at: string
  reset_cards: ResetCard[]
  reset_cards_refreshed_at: string
  current_usage?: OAuthUsage
  previous_usage?: OAuthUsage | null
}
export interface OAuthImportResult {
  imported: number
  failed: number
  total: number
  imported_files: string[]
  failed_files: { name: string; reason: string }[]
}
export interface ResetResult {
  outcome: 'reset' | 'already_redeemed' | 'nothing_to_reset' | 'no_credit'
  quota: Quota | null
  quota_error: string
  reset_cards: ResetCard[] | null
  reset_cards_refreshed_at: string
  reset_cards_error: string
  windows_reset: number
}
export const OAUTH_READ_TIMEOUT_MS = 60_000
const base = '/api/oauth/codex'
const filePath = (name: string) => `${base}/auth-files/${encodeURIComponent(name)}`
export const oauthApi = {
  start: (signal?: AbortSignal) =>
    api<{ authorization_url: string; state: string; expires_at: string }>(`${base}/session`, {
      method: 'POST',
      signal,
    }),
  callback: (callback_url: string, signal?: AbortSignal) =>
    api<{ auth_file: OAuthFile }>(`${base}/callback`, {
      method: 'POST',
      body: { callback_url },
      signal,
    }),
  files: (signal?: AbortSignal) =>
    api<{ files: OAuthFile[]; total: number; auth_dir: string }>(`${base}/auth-files`, {
      signal,
      timeoutMs: OAUTH_READ_TIMEOUT_MS,
    }),
  quota: (name: string, signal?: AbortSignal) =>
    api<Quota>(`${filePath(name)}/quota`, { signal, timeoutMs: OAUTH_READ_TIMEOUT_MS }),
  cards: (name: string, signal?: AbortSignal) =>
    api<{ reset_cards: ResetCard[]; refreshed_at: string }>(`${filePath(name)}/reset-cards`, {
      signal,
      timeoutMs: OAUTH_READ_TIMEOUT_MS,
    }),
  consume: (name: string, credit_id: string, redeem_request_id: string, signal?: AbortSignal) =>
    api<ResetResult>(`${filePath(name)}/reset-cards/consume`, {
      method: 'POST',
      body: { credit_id, redeem_request_id },
      signal,
    }),
  enabled: (name: string, enabled: boolean, signal?: AbortSignal) =>
    api<{ auth_file?: OAuthFile }>(`${filePath(name)}/${enabled ? 'enable' : 'disable'}`, {
      method: 'POST',
      signal,
    }),
  remove: (name: string, signal?: AbortSignal) =>
    api<void>(filePath(name), { method: 'DELETE', signal }),
  export: (names: string[], signal?: AbortSignal) =>
    download(`${base}/auth-files/export`, 'codex-auth-files.zip', {
      method: 'POST',
      body: { names },
      signal,
    }),
  import: (files: File[], signal?: AbortSignal) => {
    const body = new FormData()
    files.forEach((file) => body.append('files', file))
    return api<OAuthImportResult>(`${base}/auth-files/import`, { method: 'POST', body, signal })
  },
  models: (image = false, signal?: AbortSignal) =>
    api<OAuthModels>(`${base}/${image ? 'image-models' : 'models'}`, { signal }),
  addModel: (model_id: string, image = false, signal?: AbortSignal) =>
    api<OAuthModels>(`${base}/${image ? 'image-models' : 'models'}`, {
      method: 'POST',
      body: { model_id },
      signal,
    }),
  removeModel: (id: string, image = false, signal?: AbortSignal) =>
    api<OAuthModels>(`${base}/${image ? 'image-models' : 'models'}/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      signal,
    }),
  restoreModels: (image = false, signal?: AbortSignal) =>
    api<OAuthModels>(`${base}/${image ? 'image-models' : 'models'}/restore-builtins`, {
      method: 'POST',
      signal,
    }),
  defaultImageModel: (model_id: string, signal?: AbortSignal) =>
    api<OAuthModels>(`${base}/image-models/default`, { method: 'PUT', body: { model_id }, signal }),
}
