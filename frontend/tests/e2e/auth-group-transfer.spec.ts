import { expect, test } from '../browser-test'
import { mockProviderTable } from '../provider-fixtures'

test('Auth Groups 按搜索范围导出，空结果不可导出，JSON 文件导入后刷新', async ({ page }) => {
  const fixture = await mockProviderTable(page)
  const groups = ['alpha', 'beta'].map((name) => ({
    name,
    strategy: 'least_inflight',
    cooldown_seconds_on_429: 60,
    entries: [{ id: `${name}-key`, enabled: true, headers: { Authorization: 'Bearer fixture' } }],
  }))
  await page.route('**/api/auth-groups', (route) => route.fulfill({ json: groups }))
  const exported: unknown[] = []
  await page.route('**/api/auth-groups/export', async (route) => {
    const payload = route.request().postDataJSON() as { names: string[] }
    exported.push(payload)
    await route.fulfill({
      json: {
        version: 1,
        kind: 'llm_proxy.auth_groups',
        auth_groups: groups.filter((group) => payload.names.includes(group.name)),
      },
    })
  })
  const incoming = { ...groups[0]!, name: 'gamma' }
  const payload = { version: 1, kind: 'llm_proxy.auth_groups', auth_groups: [incoming] }
  let imports = 0
  await page.route('**/api/auth-groups/import', async (route) => {
    expect(route.request().postDataJSON()).toEqual(payload)
    imports++
    if (imports === 1) {
      await route.fulfill({ status: 400, json: { error: '导入校验失败' } })
      return
    }
    groups.push(incoming)
    await route.fulfill({ status: 201, json: { count: 1, names: ['gamma'] } })
  })
  await page.goto('/')
  await page.getByRole('tab', { name: /Auth Groups/ }).click()
  const search = page.getByRole('textbox', { name: '搜索配置', exact: true })
  const exportButton = page.getByRole('button', { name: '导出', exact: true })
  await search.fill('beta-key')
  const download = page.waitForEvent('download')
  await exportButton.click()
  expect((await download).suggestedFilename()).toBe('llm-proxy-auth-groups.json')
  expect(exported).toEqual([{ names: ['beta'] }])
  await search.fill('missing')
  await expect(exportButton).toBeDisabled()
  await search.fill('')
  const allDownload = page.waitForEvent('download')
  await exportButton.click()
  await allDownload
  expect(exported.at(-1)).toEqual({ names: ['alpha', 'beta'] })
  await page.getByRole('button', { name: '导入', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '导入 Auth Groups', exact: true })
  await dialog.locator('input[type=file]').setInputFiles({
    name: 'groups.json',
    mimeType: 'application/json',
    buffer: Buffer.from('\uFEFF' + JSON.stringify(payload, null, 2)),
  })
  await expect(dialog.locator('.ace_content')).toContainText('gamma')
  await dialog.getByRole('button', { name: '导入', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('导入校验失败')
  await expect(dialog.locator('.ace_content')).toContainText('gamma')
  await dialog.getByRole('button', { name: '导入', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await expect(
    page.getByRole('button', { name: '编辑 Auth Group gamma', exact: true }),
  ).toBeVisible()
  expect(imports).toBe(2)
  expect(fixture.unexpected).toEqual([])
})
