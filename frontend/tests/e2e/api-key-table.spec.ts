import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

for (const width of [1366, 1440, 1620, 1920]) {
  test(`API Key 在 ${width}px 显示全部列且无需横向滚动`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 })
    await mockAdmin(page)
    await page.addInitScript(() => {
      localStorage.setItem('table-widths:api-keys', JSON.stringify({ api_key: 600, name: 300 }))
    })
    await page.goto('/api-keys')
    await expect(page.locator('tbody tr').first()).toBeVisible()
    const table = page.locator('.ui-table-scroll')
    await expect.poll(() => table.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
    await expect(page.getByRole('columnheader', { name: /^操作/ })).toBeInViewport()
    await page.screenshot({ path: test.info().outputPath('api-key-layout.png') })
  })
}

test('API Key 多选启停与删除保留失败项，编辑不改变启用状态', async ({ page }) => {
  await mockAdmin(page)
  let rows = [1, 2].map((id) => ({
    id,
    name: `key-${id}`,
    api_key: `sk-fixture-${id}`,
    key_prefix: 'sk-fixt',
    key_suffix: '0001',
    enabled: id === 1,
    model_permissions: ['main/model-a', 'main/model-b'],
    allowed_models_count: 2,
    available_models_count: 2,
    token_limit_k: null,
    token_limit_remaining: null,
    max_concurrency: 3,
    request_quota_per_minute: 60,
    request_quota_per_day: 1000,
    total_tokens: 0,
    total_request_count: 0,
    created_at: '2026-11-06 10:00:00',
    last_used_at: null,
  }))
  await page.route('**/api/api-keys?*', (route) =>
    route.fulfill({
      json: {
        api_keys: rows,
        total: rows.length,
        available_models: ['main/model-a', 'main/model-b'],
      },
    }),
  )
  const writes: { id: number; enabled?: boolean }[] = []
  await page.route('**/api/api-keys/order', async (route) => {
    const move = route.request().postDataJSON() as { id: number; target_id: number; after: boolean }
    const moving = rows.find((row) => row.id === move.id)!
    rows = rows.filter((row) => row.id !== move.id)
    rows.splice(rows.findIndex((row) => row.id === move.target_id) + Number(move.after), 0, moving)
    await route.fulfill({ json: {} })
  })
  let failDelete = true
  await page.route(/\/api\/api-keys\/\d+$/, async (route) => {
    const id = Number(route.request().url().split('/').pop())
    if (route.request().method() === 'DELETE') {
      if (id === 2 && failDelete) {
        failDelete = false
        return route.fulfill({ status: 500, json: { error: 'Delete failed' } })
      }
      rows = rows.filter((row) => row.id !== id)
    } else {
      const body = route.request().postDataJSON()
      writes.push({ id, enabled: body.enabled })
      rows = rows.map((row) => (row.id === id ? { ...row, ...body } : row))
    }
    await route.fulfill({ json: {} })
  })
  await page.goto('/api-keys')
  for (const name of ['请求限制', '用量 / 配额', '最近使用'])
    await expect(page.getByRole('columnheader', { name })).toBeVisible()
  await expect(page.locator('.key-model').first()).toHaveText('main/model-a')
  const handle = page.getByRole('button', { name: '拖动 1 排序', exact: true })
  await handle.press('End')
  await expect(page.locator('.key-name').first()).toHaveText('key-2')
  await page.reload()
  await expect(page.locator('.key-name').first()).toHaveText('key-2')
  await handle.press('Home')
  await expect(page.locator('.key-name').first()).toHaveText('key-1')
  const key = await page.locator('.key-text').first().boundingBox()
  const eye = await page
    .getByRole('button', { name: '显示 Key', exact: true })
    .first()
    .boundingBox()
  expect(eye!.x - key!.x - key!.width).toBeLessThan(20)
  await page.getByRole('button', { name: '编辑 key-2', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '编辑 API Key' })
  await expect(dialog.getByRole('switch')).toHaveCount(0)
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  expect(writes).toEqual([{ id: 2, enabled: false }])
  writes.length = 0
  await page.getByRole('checkbox', { name: '选择当前列表', exact: true }).click()
  const bar = page.getByRole('region', { name: '批量操作' })
  await expect(bar.getByRole('button', { name: '启用', exact: true })).toBeVisible()
  await bar.getByRole('button', { name: '禁用', exact: true }).click()
  await expect(bar).toHaveCount(0)
  expect(writes).toEqual([{ id: 1, enabled: false }])
  await page.getByRole('checkbox', { name: '选择当前列表', exact: true }).click()
  await expect(bar.getByRole('button', { name: '禁用', exact: true })).toHaveCount(0)
  await bar.getByRole('button', { name: '删除', exact: true }).click()
  await page.getByRole('button', { name: '确认', exact: true }).click()
  await expect(page.locator('.ui-toast--danger')).toContainText('Delete failed')
  await expect(page.getByRole('checkbox', { name: '选择 2', exact: true })).toBeChecked()
  await expect(page.getByRole('checkbox', { name: '选择 1', exact: true })).toHaveCount(0)
  await bar.getByRole('button', { name: '删除', exact: true }).click()
  await page.getByRole('button', { name: '确认', exact: true }).click()
  await expect(bar).toHaveCount(0)
  expect(rows).toEqual([])
})

for (const width of [1440, 390]) {
  test(`API Key 多选栏完整显示且列表不移动 ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 })
    await mockAdmin(page)
    await page.goto('/api-keys')
    await expect(page.getByRole('checkbox', { name: '选择 1', exact: true })).toBeVisible()
    const before = await page.getByRole('table').boundingBox()
    await page.getByRole('checkbox', { name: '选择 1', exact: true }).click()
    const selection = page.locator('.ui-list-toolbar-selection')
    const box = (await selection.boundingBox())!
    for (const name of ['禁用', '删除']) {
      const button = (await selection.getByRole('button', { name, exact: true }).boundingBox())!
      expect(button.x).toBeGreaterThanOrEqual(box.x)
      expect(button.x + button.width).toBeLessThanOrEqual(box.x + box.width + 1)
      expect(button.y + button.height).toBeLessThanOrEqual(box.y + box.height + 1)
    }
    expect(await selection.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(
      true,
    )
    expect((await page.getByRole('table').boundingBox())!.y).toBe(before!.y)
  })
}
