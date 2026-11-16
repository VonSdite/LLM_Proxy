import type { Page } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import type { AgentBootstrap, AgentSnapshot, FileTarget } from '../src/api/agents'
import type { Bootstrap } from '../src/api/session'
import type { SystemSettings } from '../src/api/settings'
import type { Provider } from '../src/api/providers'

export const pages = [
  { path: '/', heading: 'Provider' },
  { path: '/users', heading: '用户' },
  { path: '/api-keys', heading: 'API Key' },
  { path: '/model-mappings', heading: '模型映射' },
  { path: '/oauth', heading: 'Codex OAuth' },
  { path: '/agents', heading: 'Agent 配置' },
  { path: '/statistics', heading: '统计与日志' },
  { path: '/settings', heading: '系统设置' },
]
export async function mockAdmin(page: Page, authenticated = true) {
  const bootstrap: Bootstrap = {
    version: '2.3.0',
    username: 'admin',
    auth_enabled: true,
    authenticated,
    features: { oauth: true, api_keys: true, model_mapping: true, whitelist: true },
    capabilities: { agents: true },
  }
  const settings: SystemSettings = {
    server: { host: '127.0.0.1', port: 9090 },
    admin: { username: 'admin', password: 'fixture-password' },
    client_ip: { real_ip_enabled: false, real_ip_header: 'X-Real-IP' },
    logging: { path: 'logs', level: 'INFO', llm_request_debug_enabled: false },
    oauth: {
      enabled: true,
      proxy_mode: 'direct',
      proxy: '',
      verify_ssl: false,
      safe_desensitization_enabled: false,
    },
    api_keys: { enabled: true },
    model_mapping: { enabled: true },
    auth_enabled: true,
  }
  const provider: Provider = {
    name: 'main',
    api: 'https://example.test/v1/chat/completions',
    api_key: 'fixture-key',
    auth_group: '',
    enabled: true,
    source_format: 'openai_chat',
    proxy_mode: 'direct',
    proxy: '',
    timeout_seconds: 1200,
    max_retries: 3,
    verify_ssl: false,
    force_upstream_stream: false,
    safe_desensitization_enabled: false,
    model_list: ['claude-sonnet-4-6', 'org/very-long-model-name-with-full-routing-path-20261106'],
    hook: '',
  }
  const models = provider.model_list.map((model) => `main/${model}`)
  const machine = {
    id: 'local',
    name: '本机',
    local: true,
    host: '',
    port: '',
    user: '',
    connected: true,
    platform: 'Windows',
    proxy: { mode: 'direct', url: '' },
  }
  const agent: AgentBootstrap = {
    machines: [machine],
    providers: [],
    models,
    modelLimitPresets: [
      { ids: ['claude-sonnet-4-6'], label: 'Sonnet 4.6', context: 200000, output: 64000 },
    ],
    interfaces: [{ ip: '192.168.1.10', prefix: '192.168.1.' }],
    serverHost: '127.0.0.1',
    serverPort: 9090,
    serverOrigin: 'http://127.0.0.1:5173',
    apiKeyEnabled: true,
    apiKeys: [{ name: '开发 Key', key: 'fixture-key' }],
  }
  const paths: Record<FileTarget, string> = {
    claude: 'D:/Users/demo/.claude/settings.json',
    codex: 'D:/Users/demo/.codex/config.toml',
    codexAuth: 'D:/Users/demo/.codex/auth.json',
    opencode: 'D:/Users/demo/.config/opencode/opencode.json',
  }
  const snapshot: AgentSnapshot = {
    machine,
    files: Object.fromEntries(
      Object.entries(paths).map(([target, path]) => [
        target,
        {
          path,
          defaultPath: path,
          customPath: '',
          exists: true,
          version: 'fixture-version',
          parseError: '',
          managed:
            target === 'claude'
              ? {
                  'env.ANTHROPIC_BASE_URL': 'http://127.0.0.1:9090',
                  'env.ANTHROPIC_MODEL': models[0],
                }
              : {},
        },
      ]),
    ) as AgentSnapshot['files'],
  }
  const usage = {
    total_request_count: 12,
    total_tokens: 345000,
    prompt_tokens: 300000,
    completion_tokens: 45000,
  }
  const statistics = {
    ...usage,
    id: 1,
    username: '开发用户',
    ip_address: '192.168.1.10',
    request_count: 12,
    request_model: 'smart',
    target_model_id: models[0],
    response_model: 'claude-sonnet-4-6',
    cache_read_input_tokens: 120000,
    cache_known_prompt_tokens: 300000,
    cache_usage_status: 'known',
    cache_hit_rate: 0.4,
    ip_count: 1,
    start_time: '2026-11-06 10:00:00',
    end_time: '2026-11-06 10:00:01',
    last_request_date: '2026-11-06',
    api_key_id: 1,
    key_name: '开发 Key',
    key_preview: 'sk-fixt...demo',
    outcome: 'success',
    http_status: 200,
    first_token_latency_ms: 680,
    tps: 42.5,
    duration_ms: 1250,
    success_count: 10,
    error_count: 1,
    cancelled_count: 0,
    unknown_count: 1,
    recorded_count: 12,
    success_rate: 10 / 11,
    upstream_success_rate: 10 / 11,
    upstream_success_count: 10,
    upstream_error_count: 1,
    upstream_exception_count: 2,
    upstream_cancelled_count: 0,
    upstream_unknown_count: 1,
    latency_sample_count: 8,
    tps_sample_count: 7,
    cache_sample_count: 10,
    cache_hit_count: 6,
    cache_request_hit_rate: 0.6,
  }
  const calls: { path: string; method: string; body: unknown; url: string }[] = []
  const unexpected: string[] = []
  await page.route('**/static/vendor/ace/*', async (route) => {
    const filename = new URL(route.request().url()).pathname.split('/').at(-1)!
    if (!/^[a-z_-]+\.js$/.test(filename)) {
      await route.abort()
      return
    }
    await route.fulfill({
      path: fileURLToPath(
        new URL(`../../src/presentation/static/vendor/ace/${filename}`, import.meta.url),
      ),
      contentType: 'application/javascript',
    })
  })
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      const path = url.pathname
      const method = request.method()
      const body =
        request.postData() && request.headers()['content-type']?.includes('application/json')
          ? (request.postDataJSON() as unknown)
          : undefined
      calls.push({ path, method, body, url: url.href })
      const send = (json: unknown, status = 200) => route.fulfill({ json, status })
      if (path === '/api/ui/bootstrap') return send(bootstrap)
      if (path === '/api/login') {
        if ((body as { password?: string }).password !== 'correct-password')
          return send({ error: 'Invalid username or password' }, 401)
        bootstrap.authenticated = true
        return send({ username: 'admin' })
      }
      if (path === '/api/logout') {
        bootstrap.authenticated = false
        return send({ message: 'ok' })
      }
      if (path === '/api/providers') return send(method === 'GET' ? [provider] : body)
      if (path === '/api/providers/main' && method === 'PUT') {
        Object.assign(provider, body)
        return send(provider)
      }
      if (path === '/api/auth-groups') return send([])
      if (path === '/api/providers/fetch-models')
        return send({ fetched_models: ['new-model', ...provider.model_list] })
      if (path === '/api/providers/test-models')
        return send({
          results: [
            {
              requested_model: 'model',
              available: true,
              first_token_latency_ms: 28,
              tps: 52,
              response_model: 'model',
              error: null,
            },
          ],
        })
      if (path === '/api/users')
        return send(
          method === 'GET'
            ? {
                users: [
                  {
                    id: 1,
                    username: '开发用户',
                    ip_address: '192.168.1.10',
                    whitelist_access_enabled: true,
                    model_permissions: '*',
                    allowed_models_count: 2,
                    available_models_count: 2,
                    created_at: '2026-11-06 10:00:00',
                    updated_at: '2026-11-06 10:00:00',
                    ...usage,
                  },
                ],
                total: 1,
                available_models: models,
              }
            : { id: 2 },
        )
      if (path === '/api/api-keys')
        return send({
          api_keys: [
            {
              id: 1,
              name: '开发 Key',
              api_key: 'sk-fixture-only',
              key_prefix: 'sk-fixt',
              key_suffix: 'only',
              enabled: true,
              model_permissions: '*',
              allowed_models_count: 2,
              available_models_count: 2,
              token_limit_k: 10000,
              token_limit_remaining: 9655000,
              max_concurrency: 3,
              request_quota_per_minute: 60,
              request_quota_per_day: 1000,
              created_at: '2026-11-06 10:00:00',
              last_used_at: '2026-11-06 10:00:00',
              ...usage,
            },
          ],
          total: 1,
          available_models: models,
        })
      if (path === '/api/model-mappings')
        return send(
          method === 'GET'
            ? {
                enabled: true,
                available_targets: models,
                model_mappings: [
                  {
                    id: 'smart',
                    enabled: true,
                    strategy: 'highest_priority',
                    cooldown_seconds_on_429: 60,
                    safe_desensitization_enabled: false,
                    targets: [
                      {
                        model_id: models[0],
                        priority: 1,
                        enabled: true,
                        available_model: true,
                        status: 'available',
                      },
                    ],
                  },
                ],
              }
            : body,
        )
      if (path === '/api/settings/system') return send(settings)
      if (path.startsWith('/api/settings/system/') && method === 'PUT') {
        Object.assign(settings, body)
        return send({ settings, auth_config_changed: false })
      }
      if (path === '/api/usernames') return send(['开发用户', '另一位用户'])
      if (path === '/api/statistics/daily-stats/export')
        return send({ version: 6, request_logs: [], daily_request_stats: [] })
      if (path === '/api/statistics/filter-options')
        return send({
          users: ['开发用户', '另一位用户'],
          models: ['smart', ...models],
          response_models: ['response-a', 'response-b', 'response-c'],
          ip_addresses: ['192.168.1.10', '192.168.1.20'],
        })
      if (path === '/api/request-models') return send(['smart', ...models])
      if (path === '/api/statistics/overview') {
        const end = url.searchParams.get('end_date') || '2026-11-13'
        const start = url.searchParams.get('start_date') || end
        const series: (typeof statistics & { date: string })[] = []
        for (
          const day = new Date(`${start}T12:00:00`);
          day <= new Date(`${end}T12:00:00`);
          day.setDate(day.getDate() + 1)
        ) {
          const count = [12, 28, 19, 43, 32, 54, 38][series.length % 7]!
          series.push({
            ...statistics,
            date: `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`,
            request_count: count,
            total_tokens: count * 12000,
          })
        }
        const selected = url.searchParams.getAll('request_model')
        const modelRows = [
          statistics,
          ...models.slice(0, 4).map((name, i) => ({
            ...statistics,
            request_model: name,
            total_tokens: 240000 / (i + 1),
            request_count: 9 - i,
          })),
        ].filter((row) => !selected.length || selected.includes(row.request_model))
        const modelFlows = [
          { ...statistics, target_model_id: 'provider/a', response_model: 'response-a' },
          { ...statistics, target_model_id: 'provider/b', response_model: 'response-b' },
          { ...statistics, target_model_id: 'provider/b', response_model: 'response-c' },
        ].filter((row) => !selected.length || selected.includes(row.request_model))
        return send({
          totals: statistics,
          series,
          models: modelRows,
          model_flows: modelFlows,
          response_models: modelFlows.map((row) => ({
            ...row,
            request_model: undefined,
            target_model_id: undefined,
          })),
          ips: [statistics],
        })
      }
      if (path === '/api/request-logs')
        return send({
          logs: [statistics],
          total: 123,
          page: Number(url.searchParams.get('page') || 1),
          page_size: 50,
          total_pages: 3,
        })
      if (
        [
          '/api/statistics',
          '/api/statistics/user-usage-summary',
          '/api/statistics/api-key-usage-summary',
        ].includes(path)
      )
        return send([statistics])
      if (path === '/api/statistics/export')
        return route.fulfill({
          body: 'fixture-export',
          contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          headers: { 'Content-Disposition': 'attachment; filename="statistics.xlsx"' },
        })
      if (path === '/api/oauth/codex/auth-files')
        return send({
          files: [
            {
              name: 'account-demo.json',
              path: '/data/oauth/account-demo.json',
              email: 'demo@example.test',
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
                    reset_at: '2027-01-01T00:00:00Z',
                    limit_window_seconds: 18000,
                  },
                ],
              },
              quota_error: '',
              quota_refreshed_at: '2026-11-06T10:00:00Z',
              reset_cards: [
                { id: 'card-1', title: '额度重置', expires_at: '2027-01-01T00:00:00Z' },
              ],
              reset_cards_refreshed_at: '2026-11-06T10:00:00Z',
              current_usage: {
                request_count: 12,
                total_tokens: 345000,
                estimated_cost_usd: 1.25,
                estimated_full_cost_usd: 4.46,
                limit_window_seconds: 18000,
              },
            },
          ],
          total: 1,
          auth_dir: '/data/oauth',
        })
      if (path === '/api/oauth/codex/models')
        return send({ models: [{ id: 'gpt-5.4', built_in: true }] })
      if (path === '/api/oauth/codex/image-models')
        return send({
          models: [{ id: 'gpt-image-1', built_in: true }],
          default_model: 'gpt-image-1',
        })
      if (path === '/api/agent-config/bootstrap') return send(agent)
      if (path.endsWith('/snapshot') || path.endsWith('/apply')) return send(snapshot)
      if (path.endsWith('/preview') && (body as { draft?: { values?: unknown } })?.draft?.values) {
        const definition = agent.agents?.find((item) => item.id === path.split('/').at(-2))
        const values = (body as { draft: { values: Record<string, unknown> } }).draft.values
        return send({
          token: 'fixture-ticket',
          hasChanges: true,
          rows: (definition?.fields || [])
            .filter((field) => field.id in values)
            .map((field) => ({
              file: field.file,
              path: field.path.join('.'),
              kind: 'add',
              current: null,
              next: field.component === 'secret' ? '••••••••' : values[field.id],
            })),
        })
      }
      if (path.endsWith('/preview'))
        return send({
          token: 'fixture-ticket',
          hasChanges: true,
          rows: [
            {
              file: 'claude',
              path: 'env.ANTHROPIC_MODEL',
              kind: 'change',
              current: 'old-model',
              next:
                (body as { draft?: { models?: Record<string, string> } })?.draft?.models
                  ?.ANTHROPIC_MODEL || 'new-model',
            },
          ],
        })
      if (path.endsWith('/files')) {
        const agentId = path.split('/').at(-2) as FileTarget
        const targets: FileTarget[] = agentId === 'codex' ? ['codex', 'codexAuth'] : [agentId]
        return send({
          files: targets.map((target) => ({
            target,
            path: snapshot.files[target]!.path,
            text:
              target === 'codex'
                ? 'model = "fixture-model"\n'
                : JSON.stringify({ model: 'fixture-model' }, null, 2),
            exists: true,
            version: 'fixture-version',
          })),
        })
      }
      unexpected.push(`${method} ${path}`)
      return send({ error: `Unexpected fixture request: ${method} ${path}` }, 501)
    },
  )
  return { calls, unexpected, bootstrap, snapshot, agent, provider, settings }
}
