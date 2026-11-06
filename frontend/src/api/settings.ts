import { api } from './client'

export interface SystemSettings {
  server: { host: string; port: number }
  admin: { username: string; password: string }
  client_ip: { real_ip_enabled: boolean; real_ip_header: string }
  logging: { path: string; level: string; llm_request_debug_enabled: boolean }
  oauth: {
    enabled: boolean
    proxy_mode: 'system' | 'direct' | 'custom'
    proxy: string
    verify_ssl: boolean
    safe_desensitization_enabled: boolean
  }
  api_keys: { enabled: boolean }
  model_mapping: { enabled: boolean }
  auth_enabled: boolean
}
export type SettingsGroup = 'basic' | 'client-ip' | 'debug' | 'oauth' | 'api-keys' | 'model-mapping'
export interface SettingsResult {
  settings: SystemSettings
  auth_config_changed?: boolean
  server_restart_required?: boolean
}
export const settingsApi = {
  get: (signal?: AbortSignal) => api<SystemSettings>('/api/settings/system', { signal }),
  save: (group: SettingsGroup, body: Partial<SystemSettings>, signal?: AbortSignal) =>
    api<SettingsResult>(`/api/settings/system/${group}`, { method: 'PUT', body, signal }),
}
