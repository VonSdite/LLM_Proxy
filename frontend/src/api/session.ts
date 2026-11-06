import { api } from './client'

export interface Bootstrap {
  version: string
  username: string
  auth_enabled: boolean
  authenticated: boolean
  features: { oauth: boolean; api_keys: boolean; model_mapping: boolean; whitelist: boolean }
  capabilities: { agents: boolean }
}

export const sessionApi = {
  bootstrap: () => api<Bootstrap>('/api/ui/bootstrap', { authRedirect: false }),
  login: (username: string, password: string, signal?: AbortSignal) =>
    api<{ username: string }>('/api/login', {
      method: 'POST',
      body: { username, password },
      authRedirect: false,
      signal,
    }),
  logout: () => api<{ message: string }>('/api/logout', { method: 'POST' }),
}
