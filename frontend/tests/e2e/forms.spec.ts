import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

test('Provider 创建校验字段并提交当前模型列表', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/')
  await page.getByRole('button', { name: '新增 Provider' }).click()
  const dialog = page.getByRole('dialog', { name: '新增 Provider' })
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog.getByRole('textbox', { name: 'Provider 名称', exact: true })).toHaveAttribute(
    'aria-invalid',
    'true',
  )
  await dialog.getByRole('textbox', { name: 'Provider 名称', exact: true }).fill('new_provider')
  await dialog
    .getByRole('textbox', { name: 'API 地址', exact: true })
    .fill('https://new.example/v1/chat/completions')
  await dialog.getByRole('button', { name: '添加', exact: true }).click()
  await expect(dialog.getByRole('textbox', { name: '模型 1' })).toBeFocused()
  await dialog.getByRole('textbox', { name: '模型 1' }).fill('org/new-model')
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  expect(
    fixture.calls.find((call) => call.path === '/api/providers' && call.method === 'POST')?.body,
  ).toMatchObject({
    name: 'new_provider',
    api: 'https://new.example/v1/chat/completions',
    model_list: ['org/new-model'],
  })
  expect(fixture.unexpected).toEqual([])
})

test('模型表格末行添加，眼睛图标逐项及整列切换并保存', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/')
  await page.getByRole('button', { name: '编辑 Provider main', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '编辑 Provider', exact: true })
  const table = dialog.getByRole('table')
  await page.setViewportSize({ width: 390, height: 844 })
  const add = table
    .locator('tbody > tr:last-child')
    .getByRole('button', { name: '添加', exact: true })
  await add.scrollIntoViewIfNeeded()
  const bounds = await add.boundingBox()
  expect(bounds!.x).toBeGreaterThanOrEqual(0)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390)
  await add.click()
  const model = dialog.getByRole('textbox', { name: /^模型 / }).last()
  await expect(model).toBeFocused()
  await model.fill('new-model')
  await table.getByRole('button', { name: '隐藏 new-model', exact: true }).click()
  await expect(table.getByRole('button', { name: '显示 new-model', exact: true })).toBeVisible()
  await table.locator('thead').getByRole('button', { name: '全部显示', exact: true }).click()
  await expect(table.getByRole('button', { name: '隐藏 new-model', exact: true })).toBeVisible()
  await table.locator('thead').getByRole('button', { name: '全部隐藏', exact: true }).click()
  await expect(table.locator('tbody button[aria-pressed="true"]')).toHaveCount(0)
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  const saved = fixture.calls.find(
    (call) => call.path === '/api/providers/main' && call.method === 'PUT',
  )?.body as { model_list: string[]; hidden_model_list: string[] }
  expect(saved.model_list).toContain('new-model')
  expect(saved.hidden_model_list).toEqual(saved.model_list)
  expect(fixture.unexpected).toEqual([])
})

test('Auth Group 嵌套条目保存原始凭据', async ({ page }) => {
  await mockAdmin(page)
  let saved: unknown
  await page.route('**/api/auth-groups', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    saved = route.request().postDataJSON()
    await route.fulfill({ json: saved })
  })
  await page.goto('/')
  await page.getByRole('tab', { name: /Auth Groups/ }).click()
  await page.getByRole('button', { name: '新增 Auth Group' }).click()
  const dialog = page.getByRole('dialog', { name: '新增 Auth Group' })
  await expect(dialog.getByRole('textbox', { name: /Token.*分钟|Token.*天/ })).toHaveCount(0)
  await expect(dialog.getByRole('textbox', { name: '请求 / 分钟', exact: true })).toBeVisible()
  await expect(dialog.getByRole('textbox', { name: '请求 / 天', exact: true })).toBeVisible()
  await dialog.getByRole('textbox', { name: '名称', exact: true }).fill('test-pool')
  await dialog.getByRole('textbox', { name: 'Entry ID', exact: true }).fill('entry-main')
  await dialog.getByRole('textbox', { name: 'Header 名称', exact: true }).fill('Authorization')
  await dialog.getByLabel('Header 值', { exact: true }).fill('Bearer fixture-secret')
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  expect(saved).toMatchObject({
    name: 'test-pool',
    entries: [{ id: 'entry-main', headers: { Authorization: 'Bearer fixture-secret' } }],
  })
})

test('用户创建失败标记 IP 字段，修改后保留草稿并可重试', async ({ page }) => {
  await mockAdmin(page)
  const saves: unknown[] = []
  await page.route('**/api/users', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    saves.push(route.request().postDataJSON())
    await route.fulfill(
      saves.length === 1
        ? { status: 400, json: { error: 'Invalid IP address' } }
        : { json: { id: 2 } },
    )
  })
  await page.goto('/users')
  await page.getByRole('button', { name: '添加用户' }).click()
  const dialog = page.getByRole('dialog', { name: '添加用户' })
  await dialog.getByRole('textbox', { name: '用户名', exact: true }).fill('new-user')
  await dialog.getByRole('textbox', { name: 'IP 地址', exact: true }).fill('invalid-ip')
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog.getByRole('textbox', { name: 'IP 地址', exact: true })).toHaveAttribute(
    'aria-invalid',
    'true',
  )
  await expect(dialog.getByRole('textbox', { name: '用户名', exact: true })).toHaveValue('new-user')
  await dialog.getByRole('textbox', { name: 'IP 地址', exact: true }).fill('192.168.1.22')
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  expect(saves).toHaveLength(2)
  expect(saves[1]).toEqual({ username: 'new-user', ip_address: '192.168.1.22' })
})

test('API Key 指定模型与数值配额按原值保存', async ({ page }) => {
  await mockAdmin(page)
  let saved: unknown
  await page.route('**/api/api-keys', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    saved = route.request().postDataJSON()
    await route.fulfill({ json: { api_key: {} } })
  })
  await page.goto('/api-keys')
  await page.getByRole('button', { name: '创建 API Key' }).click()
  const dialog = page.getByRole('dialog', { name: '创建 API Key' })
  await dialog.getByRole('textbox', { name: '名称', exact: true }).fill('limited-key')
  await dialog.getByRole('textbox', { name: /Token 配额/ }).fill('100')
  await dialog.getByRole('textbox', { name: '最大并发数', exact: true }).fill('2')
  await dialog.getByRole('textbox', { name: '请求 / 分钟', exact: true }).fill('30')
  await dialog.getByRole('textbox', { name: '请求 / 天', exact: true }).fill('500')
  await dialog.getByRole('tab', { name: '指定模型' }).click()
  await dialog.getByRole('checkbox', { name: 'main/claude-sonnet-4-6', exact: true }).click()
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  expect(saved).toMatchObject({
    name: 'limited-key',
    token_limit_k: 100,
    max_concurrency: 2,
    request_quota_per_minute: 30,
    request_quota_per_day: 500,
    model_permissions: ['main/claude-sonnet-4-6'],
  })
})

test('模型映射使用完整目标 ID，空白附加行不进入保存', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/model-mappings')
  await page.getByRole('button', { name: '创建映射', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '创建模型映射' })
  await dialog.getByRole('textbox', { name: '对外模型 ID', exact: true }).fill('new_mapping')
  await dialog.getByRole('combobox', { name: '目标 1', exact: true }).click()
  await page.getByRole('option', { name: 'main/claude-sonnet-4-6', exact: true }).click()
  await dialog.getByRole('button', { name: '添加目标' }).click()
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  expect(
    fixture.calls.find((call) => call.path === '/api/model-mappings' && call.method === 'POST')
      ?.body,
  ).toMatchObject({
    id: 'new_mapping',
    targets: [{ model_id: 'main/claude-sonnet-4-6', priority: 1, enabled: true }],
  })
})

test('设置开关保存后立即更新导航，数值端口不提交非法草稿', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.route('**/api/settings/system/api-keys', async (route) => {
    fixture.bootstrap.features.api_keys = false
    await route.fulfill({
      json: { settings: { api_keys: { enabled: false } }, auth_config_changed: false },
    })
  })
  await page.goto('/settings')
  const basic = page.locator('#basic')
  await basic.getByLabel('端口', { exact: true }).fill('70000')
  await basic.getByLabel('端口', { exact: true }).press('Tab')
  await expect(basic.getByLabel('端口', { exact: true })).toHaveAttribute('aria-invalid', 'true')
  await expect(basic.getByRole('button', { name: '保存', exact: true })).toBeDisabled()
  expect(fixture.calls.some((call) => call.path.endsWith('/basic'))).toBe(false)
  await basic.getByLabel('端口', { exact: true }).fill('8081')
  await basic.getByLabel('端口', { exact: true }).press('Tab')
  await basic.getByRole('button', { name: '保存', exact: true }).click()
  await expect
    .poll(() => fixture.calls.find((call) => call.path.endsWith('/basic'))?.body)
    .toMatchObject({ server: { port: 8081 } })
  const keys = page
    .locator('form')
    .filter({ has: page.getByRole('heading', { name: 'API Key', exact: true }) })
  await keys.getByRole('switch', { name: '启用 API Key 管理', exact: true }).click()
  await keys.getByRole('button', { name: '保存', exact: true }).click()
  await expect(
    page
      .getByRole('navigation', { name: '主导航' })
      .getByRole('link', { name: 'API Key', exact: true }),
  ).toHaveCount(0)
})

test('OAuth 生成授权链接且认证失败保持当前登录弹窗', async ({ page }) => {
  await mockAdmin(page)
  await page.route('**/api/oauth/codex/session', (route) =>
    route.fulfill({
      json: {
        authorization_url: 'https://auth.example/authorize?state=fixture',
        state: 'fixture',
        expires_at: '2027-01-01',
      },
    }),
  )
  await page.route('**/api/oauth/codex/callback', (route) =>
    route.fulfill({ status: 401, json: { error: 'OAuth callback failed', auth_failed: true } }),
  )
  await page.goto('/oauth')
  await page.getByRole('button', { name: '添加账号', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '添加 Codex 账号' })
  await dialog.getByRole('button', { name: '生成授权链接' }).click()
  await expect(dialog.getByRole('link', { name: '打开链接' })).toHaveAttribute(
    'href',
    'https://auth.example/authorize?state=fixture',
  )
  await dialog
    .getByLabel('回调 URL', { exact: true })
    .fill('http://localhost:1455/auth/callback?code=fixture&state=fixture')
  await dialog.getByRole('button', { name: '完成登录' }).click()
  await expect(dialog.getByRole('alert')).toContainText('OAuth callback failed')
  await expect(page).toHaveURL(/\/oauth$/)
})

test('后台会话失效跳转登录，认证关闭可直接打开管理页', async ({ page }) => {
  const fixture = await mockAdmin(page)
  fixture.bootstrap.auth_enabled = false
  await page.goto('/login')
  await expect(page).toHaveURL(/\/$/)
  fixture.bootstrap.auth_enabled = true
  fixture.bootstrap.authenticated = false
  await page.route('**/api/users?*', (route) =>
    route.fulfill({ status: 401, json: { error: 'Unauthorized' } }),
  )
  await page
    .getByRole('navigation', { name: '主导航' })
    .getByRole('link', { name: '用户', exact: true })
    .click()
  await expect(page).toHaveURL(/\/login$/)
})

test('Agent 路径修改失败保留对话框，恢复默认只发送空路径', async ({ page }) => {
  const fixture = await mockAdmin(page)
  const paths: unknown[] = []
  await page.route('**/api/agent-config/machines/local/path', async (route) => {
    paths.push(route.request().postDataJSON())
    await route.fulfill(
      paths.length === 1
        ? { status: 400, json: { error: '路径不可访问' } }
        : { json: fixture.snapshot },
    )
  })
  await page.goto('/agents')
  await page.getByRole('button', { name: 'settings.json 路径设置' }).click()
  const dialog = page.getByRole('dialog', { name: 'Claude 配置文件' })
  await dialog.getByLabel('文件路径', { exact: true }).fill('D:/custom/settings.json')
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog.getByLabel('文件路径', { exact: true })).toHaveAttribute(
    'aria-invalid',
    'true',
  )
  await dialog.getByRole('button', { name: '恢复默认' }).click()
  await expect(dialog).not.toBeVisible()
  expect(paths).toEqual([
    { target: 'claude', path: 'D:/custom/settings.json' },
    { target: 'claude', path: '' },
  ])
})
