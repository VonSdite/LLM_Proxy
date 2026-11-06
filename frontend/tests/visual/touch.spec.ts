import { expect, visualTest as test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

test.use({ hasTouch: true, viewport: { width: 390, height: 1000 } })
for (const theme of ['dark', 'light']) {
  test(`触屏共享控件 ${theme}`, async ({ page }) => {
    await mockAdmin(page)
    await page.goto('/dev/components')
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('组件展示')
    if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await expect(page).toHaveScreenshot(`touch-${theme}.png`, { fullPage: true })
  })
}
