import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

for (const [width, touch] of [
  [1920, false],
  [1440, false],
  [390, false],
  [390, true],
] as const) {
  test.describe(`${width}px ${touch ? '触屏' : '桌面'}`, () => {
    test.use({ hasTouch: touch })
    test(`API Key 明文在 ${width}px 保持单行，旧列宽不挤压按钮`, async ({ page }) => {
      await page.setViewportSize({ width, height: 1000 })
      await mockAdmin(page)
      const key = `sk-${'a'.repeat(48)}`
      await page.route('**/api/api-keys?*', (route) =>
        route.fulfill({
          json: {
            api_keys: [
              {
                id: 1,
                name: 'long-key',
                api_key: key,
                key_prefix: 'sk-aaaaaaa',
                key_suffix: 'aaaaaa',
                enabled: true,
                model_permissions: '*',
                token_limit_k: null,
              },
            ],
            total: 1,
            available_models: [],
          },
        }),
      )
      await page.addInitScript(() => {
        localStorage.setItem('table-widths:api-keys', JSON.stringify({ api_key: 100 }))
      })
      await page.goto('/api-keys')
      const row = page.locator('tbody tr').first()
      await expect(row).toContainText('long-key')
      const before = (await row.boundingBox())!.height
      await row.getByRole('button', { name: '显示 Key', exact: true }).click()
      await expect(row.locator('code')).toHaveText(key)
      expect((await row.boundingBox())!.height).toBe(before)
      const layout = await row.locator('.key-cell').evaluate((cell) => {
        const code = cell.querySelector('code')!
        const firstButton = cell.querySelector('button')!
        const range = document.createRange()
        range.selectNodeContents(code)
        return {
          lines: range.getClientRects().length,
          textRight: code.getBoundingClientRect().right,
          buttonLeft: firstButton.getBoundingClientRect().left,
          contentRight: cell.lastElementChild!.getBoundingClientRect().right,
          cellRight: cell.closest('td')!.getBoundingClientRect().right,
        }
      })
      expect(layout.lines).toBe(1)
      expect(layout.textRight).toBeLessThanOrEqual(layout.buttonLeft)
      expect(layout.contentRight).toBeLessThanOrEqual(layout.cellRight)
      await expect(page.getByRole('columnheader', { name: /输入|输出/ })).toHaveCount(0)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      )
      await row.getByRole('button', { name: '隐藏 Key', exact: true }).click()
      expect((await row.boundingBox())!.height).toBe(before)
    })
  })
}

test('API Key 限速回显、非法值标记与清空保存', async ({ page }) => {
  await mockAdmin(page)
  const saves: unknown[] = []
  await page.route('**/api/api-keys/1', (route) => {
    saves.push(route.request().postDataJSON())
    return route.fulfill({ json: { message: 'ok' } })
  })
  await page.goto('/api-keys')
  await page.getByRole('button', { name: '编辑 开发 Key', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '编辑 API Key' })
  const concurrency = dialog.getByRole('textbox', { name: '最大并发数', exact: true })
  const minute = dialog.getByRole('textbox', { name: '请求 / 分钟', exact: true })
  const day = dialog.getByRole('textbox', { name: '请求 / 天', exact: true })
  await expect(concurrency).toHaveValue('3')
  await expect(minute).toHaveValue('60')
  await expect(day).toHaveValue('1000')
  await concurrency.fill('0')
  await concurrency.press('Tab')
  await expect(concurrency).toHaveAttribute('aria-invalid', 'true')
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  expect(saves).toHaveLength(0)
  await expect(dialog).toBeVisible()
  await concurrency.fill('2')
  await minute.fill('')
  await day.fill('2500')
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  expect(saves).toHaveLength(1)
  expect(saves[0]).toMatchObject({
    max_concurrency: 2,
    request_quota_per_minute: null,
    request_quota_per_day: 2500,
  })
})
