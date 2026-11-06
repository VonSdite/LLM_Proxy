import { api, type RequestOptions } from './client'

export type AgentId = 'claude' | 'codex' | 'opencode'
export type FileTarget = AgentId | 'codexAuth'
export interface AgentMachine {
  id: string
  name: string
  local: boolean
  host: string
  port: string
  user: string
  proxy: { mode: string; url: string }
  connected: boolean
  connecting?: boolean
  platform?: string
  home?: string
  serviceHost?: string
}
export interface MachineInput {
  name: string
  host: string
  port: string
  user: string
  password: string
  proxy: { mode: string; url: string }
}
export interface FileSnapshot {
  path: string
  defaultPath: string
  defaultDirectory?: string
  customPath: string
  exists: boolean
  usesDefaultPath?: boolean
  managed: Record<string, unknown>
  version: string
  parseError: string
}
export interface AgentSnapshot {
  machine: AgentMachine
  files: Record<FileTarget, FileSnapshot>
}
export type LimitKind = 'context' | 'compact' | 'output'
export interface LimitCandidate {
  value: number
  label: string
  kind: string
  sourceUrl?: string
  checkedAt?: string
  referenceModel?: string
}
export interface ModelPreset {
  ids: string[]
  aliases?: string[]
  label: string
  context?: number
  compact?: number
  output?: number
  candidates?: Partial<Record<LimitKind, LimitCandidate[]>>
}
export interface AgentProvider {
  key: string
  name: string
  enabled: boolean
  models: {
    id: string
    contextWindow?: number
    compactThresholdPercent?: number
    maxOutputTokens?: number
  }[]
}
export interface AgentBootstrap {
  machines: AgentMachine[]
  providers: AgentProvider[]
  models: string[]
  modelLimitPresets: ModelPreset[]
  interfaces: { ip: string; prefix: string }[]
  serverHost: string
  serverPort: number
  serverOrigin: string
  apiKeyEnabled: boolean
  apiKeys: { name: string; key: string }[]
}
export interface AgentDraft {
  baseUrl: string
  apiKey: string
  model?: string
  models?: Record<string, string>
  runtime: Record<string, string>
  limits: {
    context: number | null
    compact: number | null
    output: number | null
    compactTokens?: number
    reserved?: number | null
  }
  clearFields?: string[]
}
export interface PreviewRow {
  file: string
  path: string
  kind: 'same' | 'add' | 'change' | 'remove'
  current: unknown
  next: unknown
}
export interface AgentPreview {
  token: string
  rows: PreviewRow[]
  hasChanges: boolean
}
export interface FileSource {
  target: FileTarget
  path: string
  text: string
  exists: boolean
  version: string
}
export interface PreviewInput {
  versions: Partial<Record<FileTarget, string>>
  draft?: AgentDraft
  raw?: Partial<Record<FileTarget, string>>
}

const machinePath = (id: string) => `/machines/${encodeURIComponent(id)}`
function request<T>(path: string, options: RequestOptions = {}) {
  return api<T>(`/api/agent-config${path}`, { ...options, headers: { 'X-Agent-Config': '1' } })
}
export const agentsApi = {
  bootstrap: (signal?: AbortSignal) => request<AgentBootstrap>('/bootstrap', { signal }),
  snapshot: (id: string, signal?: AbortSignal) =>
    request<AgentSnapshot>(`${machinePath(id)}/snapshot`, { signal }),
  saveMachine: (input: MachineInput, id?: string, signal?: AbortSignal) =>
    request<{ machine: AgentMachine }>(id ? machinePath(id) : '/machines', {
      method: id ? 'PUT' : 'POST',
      body: input,
      signal,
    }),
  removeMachine: (id: string, signal?: AbortSignal) =>
    request<void>(machinePath(id), { method: 'DELETE', signal }),
  connect: (id: string, trustFingerprint?: string, signal?: AbortSignal) =>
    request<AgentSnapshot>(`${machinePath(id)}/connect`, {
      method: 'POST',
      body: { trustFingerprint },
      signal,
    }),
  disconnect: (id: string, signal?: AbortSignal) =>
    request<void>(`${machinePath(id)}/disconnect`, { method: 'POST', signal }),
  path: (id: string, target: AgentId, path: string, signal?: AbortSignal) =>
    request<AgentSnapshot>(`${machinePath(id)}/path`, {
      method: 'PUT',
      body: { target, path },
      signal,
    }),
  preview: (id: string, agent: AgentId, body: PreviewInput, signal?: AbortSignal) =>
    request<AgentPreview>(`${machinePath(id)}/${agent}/preview`, { method: 'POST', body, signal }),
  apply: (id: string, token: string, signal?: AbortSignal) =>
    request<AgentSnapshot>(`${machinePath(id)}/apply`, { method: 'POST', body: { token }, signal }),
  files: (id: string, agent: AgentId, backup = 0, signal?: AbortSignal) =>
    request<{ files: FileSource[] }>(`${machinePath(id)}/${agent}/files`, {
      query: { backup },
      signal,
    }),
}
