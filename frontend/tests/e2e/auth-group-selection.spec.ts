import { expect, test } from '../browser-test'
import { mockProviderTable } from '../provider-fixtures'

test('Auth Groups 多选支持筛选全选、清空、所选导出和批量删除失败重试', async ({ page }) => {
  const fixture = await mockProviderTable(page)
  let groups = ['alpha', 'beta', 'gamma'].map((name) => ({
    name,
    strategy: 'least_inflight',
    cooldown_seconds_on_429: 60,
    entries: [{ id: `${name}-key`, enabled: true, headers: { Authorization: 'Bearer fixture' } }],
  }))
  await page.route('**/api/auth-groups', (route) => route.fulfill({ json: groups }))
  const exports: unknown[] = []
  await page.route('**/api/auth-groups/export', async (route) => {
    exports.push(route.request().postDataJSON())
    await route.fulfill({ json: { auth_groups: [] } })
  })
  const batches: unknown[] = []
  await page.route('**/api/auth-groups/batch', async (route) => {
    const payload = route.request().postDataJSON() as { action: string; names: string[] }
    batches.push(payload)
    if (batches.length === 1) {
      await route.fulfill({ status: 400, json: { error: '认证组仍被 Provider 引用' } })
    } else {
      groups = groups.filter((group) => !payload.names.includes(group.name))
      await route.fulfill({ json: { count: payload.names.length, names: payload.names } })
    }
  })
  await page.goto('/')
  await page.getByRole('tab', { name: /Auth Groups/ }).click()
  const bar = page.getByRole('region', { name: '批量操作' })
  const all = page.getByRole('checkbox', { name: '选择当前列表', exact: true })
  await expect(bar).toHaveCount(0)
  const alpha = page.getByRole('checkbox', { name: '选择 alpha', exact: true })
  await alpha.click()
  await expect(all).toHaveAttribute('data-state', 'indeterminate')
  await expect(bar).toContainText('已选 1 项')
  await bar.getByRole('button', { name: '清空选择', exact: true }).click()
  await expect(bar).toHaveCount(0)
  await expect(alpha).toBeFocused()
  await page.getByRole('textbox', { name: '搜索配置', exact: true }).fill('beta-key')
  await all.click()
  await expect(bar).toContainText('已选 1 项')
  const download = page.waitForEvent('download')
  await bar.getByRole('button', { name: '导出所选', exact: true }).click()
  expect((await download).suggestedFilename()).toBe('llm-proxy-auth-groups.json')
  expect(exports).toEqual([{ names: ['beta'] }])
  await page.getByRole('textbox', { name: '搜索配置', exact: true }).fill('')
  await expect(bar).toHaveCount(0)
  await alpha.click()
  await page.getByRole('checkbox', { name: '选择 gamma', exact: true }).click()
  await bar.getByRole('button', { name: '删除', exact: true }).click()
  await expect(page.locator('.ui-confirm')).toContainText('删除所选 2 个 Auth Group？')
  await page.getByRole('button', { name: '确认删除', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('认证组仍被 Provider 引用')
  await expect(bar).toContainText('已选 2 项')
  await expect(alpha).toBeChecked()
  await bar.getByRole('button', { name: '删除', exact: true }).click()
  await page.getByRole('button', { name: '确认删除', exact: true }).click()
  await expect(bar).toHaveCount(0)
  await expect(page.getByRole('checkbox', { name: '选择 beta', exact: true })).toBeVisible()
  await expect(page.getByRole('checkbox')).toHaveCount(2)
  expect(batches).toEqual([
    { action: 'delete', names: ['alpha', 'gamma'] },
    { action: 'delete', names: ['alpha', 'gamma'] },
  ])
  expect(fixture.unexpected).toEqual([])
})

test('Auth Groups 的选择与 Provider 隔离，刷新后移除已不存在的选择', async ({ page }) => {
  await mockProviderTable(page)
  let groups = [{ name: 'gateway', strategy: 'least_inflight', entries: [{ id: 'key-a' }] }]
  await page.route('**/api/auth-groups', (route) => route.fulfill({ json: groups }))
  await page.goto('/')
  await page.getByRole('checkbox', { name: '选择 gateway', exact: true }).click()
  await page.getByRole('tab', { name: /Auth Groups/ }).click()
  const bar = page.getByRole('region', { name: '批量操作' })
  await expect(bar).toHaveCount(0)
  await page.getByRole('checkbox', { name: '选择 gateway', exact: true }).click()
  await expect(bar).toContainText('已选 1 项')
  await page.getByRole('tab', { name: /Providers ·/ }).click()
  await expect(bar).toHaveCount(0)
  await expect(page.getByRole('checkbox', { name: '选择 gateway', exact: true })).not.toBeChecked()
  await page.getByRole('tab', { name: /Auth Groups/ }).click()
  await page.getByRole('checkbox', { name: '选择 gateway', exact: true }).click()
  groups = []
  await page.getByRole('button', { name: '刷新 Provider 与认证组', exact: true }).click()
  await expect(bar).toHaveCount(0)
  await expect(page.getByRole('checkbox', { name: '选择当前列表', exact: true })).toBeDisabled()
})
