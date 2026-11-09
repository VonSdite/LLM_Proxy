import { expect, visualTest as test } from '../browser-test'
import { mockProviderTable } from '../provider-fixtures'

for (const theme of ['dark', 'light']) {
  for (const width of [1440, 390]) {
    test(`auth-group-sidebar ${theme} ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 1000 })
      await mockProviderTable(page)
      await page.goto('/')
      if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
      await page.getByRole('tab', { name: /Auth Groups/ }).click()
      await page.getByRole('button', { name: '编辑 Auth Group team-pool', exact: true }).click()
      const dialog = page.getByRole('dialog', { name: '编辑 Auth Group', exact: true })
      const sidebar = dialog.getByRole('navigation', { name: '认证条目' })
      await sidebar.getByRole('switch').click()
      await sidebar.getByRole('button', { name: '添加条目', exact: true }).click()
      await dialog
        .getByRole('textbox', { name: 'Entry ID', exact: true })
        .fill('production-openai-backup-key')
      await sidebar.getByRole('button', { name: '添加条目', exact: true }).click()
      await expect(sidebar.getByRole('switch')).toHaveCount(3)
      const rows = await sidebar.locator('.entry-sidebar-row').all()
      for (const row of rows) {
        const bounds = (await row.boundingBox())!
        const remove = (await row.getByRole('button', { name: /移除条目/ }).boundingBox())!
        expect(remove.x + remove.width).toBeLessThanOrEqual(bounds.x + bounds.width + 1)
      }
      const add = (await sidebar
        .getByRole('button', { name: '添加条目', exact: true })
        .boundingBox())!
      const last = (await rows.at(-1)!.boundingBox())!
      expect(add.y).toBeGreaterThanOrEqual(last.y + last.height)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      )
      await dialog.getByRole('heading', { name: '编辑 Auth Group', exact: true }).click()
      await page.mouse.move(0, 0)
      await expect(page).toHaveScreenshot(`auth-group-sidebar-${theme}-${width}.png`, {
        fullPage: true,
      })
    })
  }
}
