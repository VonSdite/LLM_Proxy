import { expect, visualTest as test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'
import { mockProviderTable } from '../provider-fixtures'

for (const width of [1440, 390]) {
  for (const theme of ['dark', 'light']) {
    test(`provider auth entry toolbar ${theme} ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 1000 })
      await mockProviderTable(page)
      await page.goto('/')
      if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
      await page
        .getByRole('button', { name: '编辑 Provider api_responses_tt', exact: true })
        .click()
      const dialog = page.getByRole('dialog', { name: '编辑 Provider', exact: true })
      const entry = dialog.getByRole('combobox', { name: '测试 Auth Entry', exact: true })
      await entry.scrollIntoViewIfNeeded()
      await expect(entry).toContainText('key-a')
      const box = (await entry.boundingBox())!
      const tidy = (await dialog.getByRole('button', { name: '整理', exact: true }).boundingBox())!
      expect(box.x).toBeGreaterThanOrEqual(0)
      expect(box.x + box.width).toBeLessThanOrEqual(width)
      if (width > 900) {
        expect(box.x + box.width).toBeLessThan(tidy.x)
        expect(Math.abs(box.y + box.height / 2 - tidy.y - tidy.height / 2)).toBeLessThan(2)
      }
      await page.mouse.move(0, 0)
      await expect(page).toHaveScreenshot(`provider-auth-entry-${theme}-${width}.png`)
    })
  }
}

for (const theme of ['dark', 'light']) {
  test(`provider model results ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 })
    await mockAdmin(page)
    let attempt = 0
    await page.route('**/api/providers/test-models', async (route) => {
      const available = ++attempt === 1
      await route.fulfill({
        json: {
          results: [
            {
              requested_model: 'model',
              available,
              first_token_latency_ms: available ? 1746.64 : null,
              tps: available ? 59.12 : null,
              response_model: 'model',
              error: available
                ? null
                : "The requested model does not exist. Please check the 'model' parameter in your request.",
            },
          ],
        },
      })
    })
    await page.goto('/')
    if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
    await page.getByRole('button', { name: '编辑 Provider main', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: '编辑 Provider', exact: true })
    const rows = dialog.locator('tbody > tr[data-sort-id]')
    for (const row of await rows.all())
      await row.getByRole('button', { name: '测试', exact: true }).click()
    await expect(rows.nth(1)).toContainText('失败')
    await dialog.getByRole('checkbox', { name: '选择 1', exact: true }).click()
    await page.mouse.move(0, 0)
    await expect(page).toHaveScreenshot(`provider-model-results-${theme}.png`)
  })
}

for (const width of [1440, 768, 390]) {
  for (const theme of ['dark', 'light']) {
    test(`provider custom proxy ${theme} ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 1000 })
      const fixture = await mockAdmin(page)
      fixture.provider.verify_ssl = true
      fixture.provider.safe_desensitization_enabled = true
      fixture.provider.hook = 'routing/main.py'
      await page.goto('/')
      if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
      await page.getByRole('button', { name: '编辑 Provider main', exact: true }).click()
      const dialog = page.getByRole('dialog', { name: '编辑 Provider', exact: true })
      await dialog.getByRole('combobox', { name: '连接方式', exact: true }).click()
      await page.getByRole('option', { name: '自定义代理', exact: true }).click()
      await dialog
        .getByRole('textbox', { name: '代理地址', exact: true })
        .fill('socks5h://127.0.0.1:1080')
      await dialog.getByRole('switch', { name: '安全脱敏', exact: true }).scrollIntoViewIfNeeded()
      await page.mouse.move(0, 0)
      await expect(page).toHaveScreenshot(`provider-proxy-${theme}-${width}.png`)
      await dialog.getByRole('button', { name: '认证', exact: true }).click()
      const popup = page.getByRole('dialog', { name: '代理认证', exact: true })
      await expect(popup.locator('.ui-popover-arrow')).toBeVisible()
      await popup.getByRole('textbox', { name: '代理用户名', exact: true }).fill('proxy-user')
      await popup.getByRole('textbox', { name: '代理密码', exact: true }).fill('sample@password')
      await page.mouse.move(0, 0)
      await expect(page).toHaveScreenshot(`provider-proxy-auth-${theme}-${width}.png`)
      expect(fixture.unexpected).toEqual([])
    })
  }
}
