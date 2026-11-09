import type { Page } from '@playwright/test'
import type { OAuthFile } from '../src/api/oauth'
import { mockAdmin } from './browser-fixtures'

export async function mockOAuthCards(page: Page) {
  const fixture = await mockAdmin(page)
  const account = (name: string, overrides: Partial<OAuthFile> = {}): OAuthFile => ({
    name: `codex-${name}@example.test.json`,
    path: `/data/oauth/codex-${name}@example.test.json`,
    email: `${name}@example.test`,
    plan_type: 'plus',
    enabled: true,
    disabled: false,
    availability_status: 'available',
    availability_status_message: '',
    availability_retry_at: null,
    usage_status_message: '',
    expired: '2027-01-01T00:00:00Z',
    quota: {
      windows: [
        {
          label: 'Codex 5h',
          remaining_percent: 72,
          reset_at: '2026-11-07T05:00:00Z',
          limit_window_seconds: 18000,
        },
        {
          label: 'Codex 7d',
          remaining_percent: 34,
          reset_at: '2026-11-12T00:00:00Z',
          limit_window_seconds: 604800,
        },
      ],
    },
    quota_error: '',
    quota_refreshed_at: '2026-11-07T01:52:00Z',
    reset_cards: [],
    reset_cards_refreshed_at: '2026-11-07T01:52:00Z',
    current_usage: {
      request_count: 128,
      total_tokens: 3456000,
      estimated_cost_usd: 3.42,
      estimated_full_cost_usd: 12.21,
      limit_window_seconds: 18000,
    },
    ...overrides,
  })
  const files = [
    account('studio', {
      reset_cards: [{ id: 'credit', title: '额度重置', expires_at: '2026-12-01T00:00:00Z' }],
    }),
    account('lab+free', {
      plan_type: 'free',
      quota: {
        windows: [
          {
            label: 'Codex 30 天',
            remaining_percent: 100,
            reset_at: '2026-11-24T00:00:00Z',
            limit_window_seconds: 2592000,
          },
        ],
      },
      current_usage: {
        request_count: 0,
        total_tokens: 0,
        estimated_cost_usd: 0,
        estimated_full_cost_usd: null,
      },
    }),
    account('very-long-authentication-file-name+disabled', {
      enabled: false,
      disabled: true,
      plan_type: 'free',
      availability_status: 'auth_failed',
      quota_error:
        'Codex auth file authentication failed; quota unavailable: Could not parse your authentication token. Please try signing in again.',
      quota_refreshed_at: '2026-10-26T14:50:25Z',
      quota: {
        windows: [
          {
            label: 'Codex 30 天',
            remaining_percent: 100,
            reset_at: '2026-10-24T15:20:23Z',
            limit_window_seconds: 2592000,
          },
        ],
      },
    }),
    account('expired', {
      availability_status: 'auth_failed',
      quota: null,
      quota_error: 'The authentication token has expired. Please sign in again.',
    }),
    account('limited', {
      availability_status: 'quota_cooldown',
      quota: {
        windows: [
          {
            label: 'Codex 5h',
            remaining_percent: 0,
            reset_at: '2026-11-07T05:00:00Z',
            limit_window_seconds: 18000,
          },
        ],
      },
    }),
    account('unknown', { availability_status: 'unknown', quota: null }),
  ]
  await page.route('**/api/oauth/codex/auth-files', (route) =>
    route.fulfill({ json: { files, total: files.length, auth_dir: '/data/oauth' } }),
  )
  return { ...fixture, files }
}
