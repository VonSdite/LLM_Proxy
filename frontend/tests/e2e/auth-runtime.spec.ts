import { expect, test } from '../browser-test'
import { mockAuthRuntime } from '../auth-runtime-fixtures'

test('运行状态直接显示完整结果和上限，刷新与重置保持当前窗口和表格', async ({ page }) => {
  const fixture = await mockAuthRuntime(page)
  await page.goto('/')
  await page.getByRole('tab', { name: /Auth Groups/ }).click()
  await page.getByRole('button', { name: '运行状态', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'team-pool · 运行状态', exact: true })
  const manual = dialog
    .getByRole('row')
    .filter({ has: page.getByRole('cell', { name: 'manual', exact: true }) })
  await expect(manual).toContainText('已禁用')
  await expect(manual).not.toContainText('manual_disabled')
  await expect(manual).not.toContainText('禁用原因')
  await expect(dialog.getByRole('button', { name: '详情', exact: true })).toHaveCount(0)
  const expired = dialog
    .getByRole('row')
    .filter({ has: page.getByRole('cell', { name: 'expired-key', exact: true }) })
  await expect(expired).toContainText('HTTP 401')
  await expect(expired).toContainText(fixture.runtime.entries[1]!.last_error_message!)
  await expect(expired).toContainText('12 / 60')
  await expect(expired).toContainText('360 / 2,000')
  await expect(expired).toContainText('2026/11/9')
  const refresh = dialog.getByRole('button', { name: '刷新运行状态', exact: true })
  await expect(refresh).toHaveText('')
  const original = await expired.elementHandle()
  const top = (await dialog.boundingBox())!.y
  fixture.runtime.entries[1]!.last_error_message = 'Updated upstream error after refresh'
  await refresh.click()
  await expect(expired).toContainText('Updated upstream error after refresh')
  expect(await expired.evaluate((row, previous) => row === previous, original)).toBe(true)
  expect((await dialog.boundingBox())!.y).toBe(top)
  expect(fixture.reads()).toBe(2)
  await page.route('**/api/auth-groups/team-pool/entries/expired-key/reset', async (route) => {
    Object.assign(fixture.runtime.entries[1]!, {
      disabled: false,
      status: 'available',
      disabled_reason: null,
      last_error_message: null,
      last_status_code: null,
      minute_request_count: 0,
      day_request_count: 0,
    })
    await route.fulfill({ json: { message: 'ok' } })
  })
  await expired.getByRole('button', { name: '重置', exact: true }).click()
  await page.locator('.ui-confirm').getByRole('button', { name: '重置', exact: true }).click()
  await expect(expired).toContainText('可用')
  await expect(expired).not.toContainText('Updated upstream error after refresh')
  await expect(expired).toContainText('0 / 60')
  await expect(page.getByRole('dialog')).toHaveCount(1)
  expect(fixture.unexpected).toEqual([])
})

test('短窗口中大量条目仍从上方打开，表格在弹窗内部滚动', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 480 })
  const fixture = await mockAuthRuntime(page)
  fixture.runtime.entries = Array.from({ length: 24 }, (_, index) => ({
    ...fixture.runtime.entries[1]!,
    id: `entry-${index}`,
  }))
  await page.goto('/')
  await page.getByRole('tab', { name: /Auth Groups/ }).click()
  await page.getByRole('button', { name: '运行状态', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'team-pool · 运行状态', exact: true })
  await expect(dialog.getByRole('row')).toHaveCount(25)
  const box = (await dialog.boundingBox())!
  expect(box.y).toBeGreaterThanOrEqual(24)
  expect(box.y).toBeLessThanOrEqual(96)
  expect(box.y + box.height).toBeLessThanOrEqual(480 - 24 + 1)
  expect(
    await dialog
      .locator('.ui-dialog-body')
      .evaluate((node) => node.scrollHeight > node.clientHeight),
  ).toBe(true)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
})
