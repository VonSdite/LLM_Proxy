import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

test('Provider 拉取模型使用当前表单，嵌套选择后可测试并保存', async ({ page }) => {
  const fixture = await mockAdmin(page)
  let fetchedUrl = ''
  let tested: unknown
  await page.route('**/api/providers/fetch-models?*', async (route) => {
    fetchedUrl = route.request().url()
    await route.fulfill({ json: { fetched_models: ['claude-sonnet-4-6', 'org/new-model'] } })
  })
  await page.route('**/api/providers/test-models', async (route) => {
    tested = route.request().postDataJSON()
    await route.fulfill({
      json: {
        results: [
          {
            requested_model: 'org/new-model',
            available: true,
            first_token_latency_ms: 50,
            tps: 20,
            response_model: 'org/new-model',
            error: null,
          },
        ],
      },
    })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '编辑 main', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '编辑 Provider', exact: true })
  await dialog
    .getByRole('textbox', { name: 'API 地址', exact: true })
    .fill('https://draft.example/v1/chat/completions')
  await dialog.getByRole('button', { name: '拉取模型', exact: true }).click()
  const picker = page.getByRole('dialog', { name: '添加上游模型', exact: true })
  await picker.getByRole('checkbox', { name: 'org/new-model', exact: true }).click()
  await picker.getByRole('button', { name: '添加所选', exact: true }).click()
  await expect(picker).toHaveCount(0)
  expect(new URL(fetchedUrl).searchParams.get('api')).toBe(
    'https://draft.example/v1/chat/completions',
  )
  const row = dialog
    .getByRole('row')
    .filter({ has: page.getByRole('textbox', { name: '模型 3', exact: true }) })
  await expect(row.getByRole('textbox')).toHaveValue('org/new-model')
  await row.getByRole('button', { name: '测试', exact: true }).click()
  await expect(row).toContainText('50 ms')
  await expect(row).toContainText('20 tok/s')
  expect(tested).toMatchObject({
    api: 'https://draft.example/v1/chat/completions',
    models: ['org/new-model'],
  })
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  expect(
    fixture.calls.find((call) => call.path === '/api/providers/main' && call.method === 'PUT')
      ?.body,
  ).toMatchObject({
    model_list: [
      'claude-sonnet-4-6',
      'org/very-long-model-name-with-full-routing-path-20261106',
      'org/new-model',
    ],
  })
  expect(fixture.unexpected).toEqual([])
})

test('Provider JSON 文件导入、所选导出与批量禁用保留请求范围', async ({ page }) => {
  const fixture = await mockAdmin(page)
  const payload = {
    providers: [{ name: 'imported', api: 'https://example.test/v1', model_list: ['org/model'] }],
  }
  let imported: unknown
  let exported: unknown
  let batch: unknown
  await page.route('**/api/providers/import', async (route) => {
    imported = route.request().postDataJSON()
    await route.fulfill({ json: { count: 1, auth_groups_count: 0 } })
  })
  await page.route('**/api/providers/export', async (route) => {
    exported = route.request().postDataJSON()
    await route.fulfill({
      body: JSON.stringify(payload),
      contentType: 'application/json',
      headers: { 'Content-Disposition': 'attachment; filename="providers.json"' },
    })
  })
  await page.route('**/api/providers/batch', async (route) => {
    batch = route.request().postDataJSON()
    await route.fulfill({ json: { success: true } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '导入', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '导入 Provider', exact: true })
  await dialog.locator('input[type="file"]').setInputFiles({
    name: 'providers.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(payload)),
  })
  await dialog.getByRole('button', { name: '导入', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  expect(imported).toEqual(payload)
  await page.getByRole('checkbox', { name: '选择 main', exact: true }).click()
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出所选', exact: true }).click()
  expect((await download).suggestedFilename()).toBe('providers.json')
  expect(exported).toEqual({ names: ['main'] })
  await page.getByRole('button', { name: '禁用', exact: true }).click()
  await expect.poll(() => batch).toEqual({ names: ['main'], action: 'disable' })
  await expect(page.getByRole('checkbox', { name: '选择 main', exact: true })).not.toBeChecked()
  expect(fixture.unexpected).toEqual([])
})

test('OAuth 卡片刷新、重置确认和所选导出遵循现有请求链路', async ({ page }) => {
  const fixture = await mockAdmin(page)
  const reads: string[] = []
  let consume: unknown
  let exported: unknown
  await page.route('**/api/oauth/codex/auth-files/account-demo.json/quota', async (route) => {
    reads.push('quota')
    await route.fulfill({ json: { windows: [], refreshed_at: '2026-11-06T10:00:00Z' } })
  })
  await page.route('**/api/oauth/codex/auth-files/account-demo.json/reset-cards', async (route) => {
    reads.push('cards')
    await route.fulfill({
      json: {
        reset_cards: [{ id: 'card-1', title: '额度重置', expires_at: '2027-01-01T00:00:00Z' }],
        refreshed_at: '2026-11-06T10:00:00Z',
      },
    })
  })
  await page.route(
    '**/api/oauth/codex/auth-files/account-demo.json/reset-cards/consume',
    async (route) => {
      consume = route.request().postDataJSON()
      await route.fulfill({
        json: { outcome: 'reset', reset_cards: [], reset_cards_error: '', quota: null },
      })
    },
  )
  await page.route('**/api/oauth/codex/auth-files/export', async (route) => {
    exported = route.request().postDataJSON()
    await route.fulfill({ body: 'fixture-zip', contentType: 'application/zip' })
  })
  await page.goto('/oauth')
  await page.getByRole('button', { name: '刷新 account-demo.json 额度', exact: true }).click()
  await expect(
    page.getByRole('button', { name: '刷新 account-demo.json 额度', exact: true }),
  ).toBeEnabled()
  expect(reads).toEqual(['quota', 'cards'])
  await page.locator('.oauth-file').getByRole('button', { name: '使用', exact: true }).click()
  expect(consume).toBeUndefined()
  await page.locator('.ui-confirm').getByRole('button', { name: '使用', exact: true }).click()
  await expect
    .poll(() => consume)
    .toMatchObject({ credit_id: 'card-1', redeem_request_id: expect.any(String) })
  await expect(
    page.getByRole('button', { name: '刷新 account-demo.json 额度', exact: true }),
  ).toBeEnabled()
  expect(reads).toEqual(['quota', 'cards', 'quota'])
  await page.getByRole('checkbox', { name: '选择 account-demo.json', exact: true }).click()
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出', exact: true }).click()
  expect((await download).suggestedFilename()).toBe('codex-auth-files.zip')
  expect(exported).toEqual({ names: ['account-demo.json'] })
  expect(fixture.unexpected).toEqual([])
})
