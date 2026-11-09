import { expect, visualTest as test } from '../browser-test'
import { mockAuthRuntime } from '../auth-runtime-fixtures'

for (const theme of ['dark', 'light']) {
  for (const width of [1920, 1440, 390]) {
    test(`auth-runtime ${theme} ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 1000 })
      await mockAuthRuntime(page)
      await page.goto('/')
      if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
      await page.getByRole('tab', { name: /Auth Groups/ }).click()
      await page.getByRole('button', { name: '运行状态', exact: true }).click()
      const dialog = page.getByRole('dialog', { name: 'team-pool · 运行状态', exact: true })
      await expect(dialog.getByRole('cell', { name: 'expired-key', exact: true })).toBeVisible()
      const box = (await dialog.boundingBox())!
      expect(box.y).toBe(96)
      if (width >= 1440) {
        expect(
          await dialog
            .locator('.ui-table-scroll')
            .evaluate((node) => node.scrollWidth - node.clientWidth),
        ).toBeLessThanOrEqual(1)
      } else {
        await dialog.locator('.ui-table-scroll').evaluate((node) => {
          node.scrollLeft = node.scrollWidth
        })
        await expect(dialog.getByText('HTTP 401', { exact: true })).toBeVisible()
      }
      await page.mouse.move(0, 0)
      await expect(page).toHaveScreenshot(`auth-runtime-${theme}-${width}.png`, { fullPage: true })
      await dialog.getByRole('button', { name: '重置', exact: true }).first().click()
      const confirmation = page.locator('.ui-confirm')
      await expect(confirmation.getByRole('listitem')).toHaveCount(2)
      await expect(confirmation.locator('.ui-popover-arrow')).toBeVisible()
      await page.mouse.move(0, 0)
      await expect(page).toHaveScreenshot(`auth-runtime-reset-${theme}-${width}.png`, {
        fullPage: true,
      })
    })
  }
}
