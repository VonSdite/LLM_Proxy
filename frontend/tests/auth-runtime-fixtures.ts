import type { Page } from '@playwright/test'
import type { AuthRuntime, AuthRuntimeEntry } from '../src/api/providers'
import { mockProviderTable } from './provider-fixtures'

export async function mockAuthRuntime(page: Page) {
  const fixture = await mockProviderTable(page)
  const base: AuthRuntimeEntry = {
    id: 'primary',
    enabled: true,
    disabled: false,
    status: 'available',
    inflight: 1,
    max_concurrency: 4,
    request_quota_per_minute: 60,
    request_quota_per_day: 2000,
    disabled_reason: null,
    cooldown_until: null,
    last_status_code: 200,
    last_error_type: null,
    last_error_message: null,
    updated_at: '2026-11-09T09:10:00',
    minute_request_count: 12,
    day_request_count: 360,
    minute_total_tokens: 18000,
    day_total_tokens: 640000,
  }
  const runtime: AuthRuntime = {
    name: 'team-pool',
    summary: { entry_count: 4, available_count: 1, disabled_count: 2, cooldown_count: 1 },
    entries: [
      {
        ...base,
        id: 'manual',
        status: 'disabled',
        disabled: true,
        disabled_reason: 'manual_disabled',
        last_status_code: null,
        inflight: 0,
        updated_at: null,
      },
      {
        ...base,
        id: 'expired-key',
        status: 'disabled',
        disabled: true,
        disabled_reason: 'http_401',
        last_status_code: 401,
        inflight: 0,
        last_error_message:
          'Authentication failed: the supplied API key has expired.\n请在认证组中更新凭据后重新启用。',
      },
      {
        ...base,
        id: 'rate-limited',
        status: 'cooldown',
        last_status_code: 429,
        inflight: 0,
        cooldown_until: '2026-11-09T09:11:00',
        last_error_type: 'rate_limit_exceeded',
        last_error_message: 'Too many requests. Retry after 60 seconds.',
      },
      base,
    ],
  }
  let reads = 0
  await page.route('**/api/auth-groups/team-pool/runtime', async (route) => {
    reads++
    await route.fulfill({ json: runtime })
  })
  return { ...fixture, runtime, reads: () => reads }
}
