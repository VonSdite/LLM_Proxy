import type { Page } from '@playwright/test'
import type { Provider } from '../src/api/providers'
import { mockAdmin } from './browser-fixtures'

export async function mockProviderTable(page: Page) {
  const fixture = await mockAdmin(page)
  const providers: Provider[] = [
    {
      ...fixture.provider,
      name: 'gateway',
      api: 'https://llm-gateway-production.apigateway-cn-beijing.volceapi.com/v1/chat/completions',
      verify_ssl: true,
      force_upstream_stream: true,
      safe_desensitization_enabled: true,
      hook: 'routing/gateway.py',
      model_list: [
        'deepseek-v3.2',
        'moonshotai/kimi-k2.5',
        'glm-5',
        'deepseek-v4-flash',
        'deepseek-v4-pro',
        'gpt-5.4',
        'claude-sonnet-4-6',
        'qwen3.5-397b',
      ],
    },
    {
      ...fixture.provider,
      name: 'api_responses_tt',
      api: 'https://api.example.test/v1/responses',
      source_format: 'openai_responses',
      verify_ssl: true,
      auth_group: 'team-pool',
      model_list: ['gpt-5.4', 'gpt-5.4-mini'],
    },
    {
      ...fixture.provider,
      name: 'claude',
      api: 'https://anthropic-relay-production.apigateway-cn-beijing.volceapi.com/anthropic/v1/messages',
      source_format: 'claude_chat',
      force_upstream_stream: true,
      hook: 'routing/anthropic/long_custom_request_and_response_hook.py',
      api_key: null,
      auth_group: 'team-pool',
      model_list: ['claude-opus-4-6', 'claude-sonnet-4-6', 'claude-haiku-4-5'],
      hidden_model_list: ['claude-haiku-4-5'],
    },
    {
      ...fixture.provider,
      name: 'local',
      api: 'http://127.0.0.1:11434/v1/chat/completions',
      api_key: null,
      model_list: ['qwen3.5:35b', 'org/very-long-model-name-with-full-routing-path-20261106'],
    },
    {
      ...fixture.provider,
      name: 'backup',
      api: 'https://regional-fallback-inference-service.apigateway-cn-beijing.volceapi.com/openai/v1/chat/completions',
      enabled: false,
      model_list: ['deepseek-v3.2'],
    },
  ]
  const group = {
    name: 'team-pool',
    strategy: 'least_inflight',
    cooldown_seconds_on_429: 60,
    entries: [{ id: 'key-a', enabled: true, headers: { Authorization: 'Bearer fixture-only' } }],
    summary: { entry_count: 1, available_count: 1, disabled_count: 0, cooldown_count: 0 },
    provider_count: 2,
  }
  await page.route('**/api/providers', (route) => route.fulfill({ json: providers }))
  await page.route('**/api/auth-groups', (route) => route.fulfill({ json: [group] }))
  await page.route('**/api/auth-groups/team-pool/runtime', (route) =>
    route.fulfill({
      json: {
        ...group,
        entries: [{ ...group.entries[0], disabled: false, status: 'available', inflight: 0 }],
      },
    }),
  )
  return { ...fixture, providers }
}
