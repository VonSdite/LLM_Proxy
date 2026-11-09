import { expect, test } from '../browser-test'
import { mockOAuthCards } from '../oauth-fixtures'

test('认证文件默认可用、全部位于末项，搜索与全选遵循当前筛选', async ({ page }) => {
  const fixture = await mockOAuthCards(page)
  await page.goto('/oauth')
  await expect(page.getByRole('tab', { name: '可用 2', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await expect(page.getByRole('tab')).toHaveText([
    '可用 2',
    '额度用完 1',
    '认证失败 1',
    '已禁用 1',
    '全部 6',
  ])
  await expect(page.getByRole('article')).toHaveCount(2)
  await page.getByRole('checkbox', { name: /当前筛选/ }).click()
  await page.getByRole('tab', { name: '全部 6', exact: true }).click()
  await expect(page.getByRole('article')).toHaveCount(6)
  await expect(page.getByRole('article').getByRole('checkbox', { checked: true })).toHaveCount(2)
  await page.getByRole('textbox', { name: '搜索认证文件' }).fill('very-long')
  await expect(page.getByRole('article')).toHaveCount(1)
  await expect(page.getByRole('article')).toHaveAttribute('aria-label', fixture.files[2]!.name)
  await page.reload()
  await expect(page.getByRole('tab', { name: '可用 2', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  expect(fixture.unexpected).toEqual([])
})

for (const width of [1440, 390]) {
  test(`${width}px 的单窗口额度铺满卡片，历史快照与完整错误可读`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 })
    await page.clock.install({ time: new Date('2026-11-07T02:00:00Z') })
    const fixture = await mockOAuthCards(page)
    await page.goto('/oauth')
    const free = page.getByRole('article', { name: fixture.files[1]!.name, exact: true })
    const track = free.getByRole('progressbar')
    await expect(track).toHaveAttribute('aria-valuenow', '100')
    const quotaWidth = (await free.locator('.quota-windows').boundingBox())!.width
    expect((await track.boundingBox())!.width).toBeGreaterThanOrEqual(quotaWidth - 2)
    await expect(free.locator('footer')).toHaveCount(0)
    await expect(page.getByText('无可用重置卡')).toHaveCount(0)
    await page.getByRole('tab', { name: '全部 6', exact: true }).click()
    const disabled = page.getByRole('article', { name: fixture.files[2]!.name, exact: true })
    await expect(disabled).toContainText('上次额度')
    await expect(disabled).toContainText('等待刷新')
    await expect(disabled).toContainText('认证失败，请重新登录')
    await expect(disabled.getByText(fixture.files[2]!.quota_error, { exact: true })).toHaveCount(0)
    await disabled.getByRole('button', { name: '查看原因', exact: true }).click()
    await expect(disabled.getByText(fixture.files[2]!.quota_error, { exact: true })).toBeVisible()
    await disabled.getByRole('button', { name: '收起', exact: true }).click()
    const name = disabled.locator('.oauth-file-name')
    await expect(name).toHaveAttribute('title', fixture.files[2]!.name)
    await expect(name).toHaveCSS('text-overflow', 'ellipsis')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}

test('没有可用账号时保留可用筛选，并提供查看全部入口', async ({ page }) => {
  const fixture = await mockOAuthCards(page)
  fixture.files.splice(0, 2)
  await page.goto('/oauth')
  await expect(page.getByRole('tab', { name: '可用 0', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await expect(page.getByText('暂无可用账号', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '查看全部', exact: true }).click()
  await expect(page.getByRole('article')).toHaveCount(4)
})
