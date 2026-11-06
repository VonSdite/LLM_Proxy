import type { OAuthFile, Quota, ResetCard } from '@/api/oauth'
export const OAUTH_SYNC_INTERVAL_MS = 60_000
export const MAX_TIMER_DELAY_MS = 2 ** 31 - 1
export function timestamp(value: number | string | null | undefined): number | null {
  if (value == null || String(value).trim() === '') return null
  const numeric = Number(value)
  if (Number.isFinite(numeric)) return numeric > 100_000_000_000 ? numeric : numeric * 1000
  const parsed = Date.parse(String(value))
  return Number.isFinite(parsed) ? parsed : null
}
export function quotaWindows(quota?: Quota | null) {
  const all = quota?.windows || []
  const codex = all.filter((window) => window.label.toLowerCase().startsWith('codex'))
  return codex.length ? codex : all.slice(0, 1)
}
export function quotaDue(file: OAuthFile, now = Date.now()) {
  if (!file.quota) return false
  const updated = timestamp(file.quota_refreshed_at || file.quota.refreshed_at)
  const all = file.quota.windows || []
  const codex = all.filter((window) => window.label.toLowerCase().startsWith('codex'))
  return (codex.length ? codex : all).some((window) => {
    const reset = timestamp(window.reset_at)
    return reset !== null && reset <= now && (updated === null || updated <= reset)
  })
}
export function exhausted(file: OAuthFile, now = Date.now()) {
  if (file.quota_error) return false
  return (file.quota?.windows || [])
    .filter((window) => window.label.toLowerCase().startsWith('codex'))
    .some((window) => {
      const empty =
        (window.remaining_percent != null && Number(window.remaining_percent) <= 0) ||
        (window.used_percent != null && Number(window.used_percent) >= 100)
      const reset = timestamp(window.reset_at)
      return empty && (reset === null || reset > now)
    })
}
export type FileFilter =
  'all' | 'available' | 'quota_exhausted' | 'auth_failed' | 'disabled' | 'other_unavailable'
export function fileCategory(file: OAuthFile, now = Date.now()): FileFilter {
  if (file.enabled === false || file.disabled) return 'disabled'
  if (file.availability_status === 'auth_failed') return 'auth_failed'
  if (['quota_cooldown', 'quota_exhausted'].includes(file.availability_status))
    return 'quota_exhausted'
  if (file.availability_status === 'filtered') return 'other_unavailable'
  if (exhausted(file, now)) return 'quota_exhausted'
  if (['available', 'refresh_required', 'auth_check_required'].includes(file.availability_status))
    return 'available'
  return 'other_unavailable'
}
export const fileLabels: Record<FileFilter, string> = {
  all: '全部',
  available: '可用',
  quota_exhausted: '额度用完',
  auth_failed: '认证失败',
  disabled: '已禁用',
  other_unavailable: '其他',
}
export function availableCards(cards: ResetCard[], now = Date.now()) {
  return cards.filter((card) => {
    const expiry = timestamp(card.expires_at)
    return expiry === null || expiry > now
  })
}
export function cost(value: number | null | undefined) {
  return value == null
    ? '—'
    : `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}
export function windowName(seconds?: number) {
  if (!seconds || seconds <= 0) return '周期'
  if (seconds % 86400 === 0) return `${seconds / 86400} 天`
  if (seconds % 3600 === 0) return `${seconds / 3600} 小时`
  return `${Math.round(seconds / 60)} 分钟`
}
