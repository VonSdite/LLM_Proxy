import { expect, visualTest as test } from '../browser-test'
import { mockAdmin, pages } from '../browser-fixtures'

for (const width of [2560, 1920, 1440, 1024, 390]) {
  for (const theme of ['dark', 'light']) {
    for (const item of [
      ...pages,
      { path: '/login', heading: '登录' },
      { path: '/dev/components', heading: '组件展示' },
    ]) {
      test(`${item.path} ${theme} ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1000 })
        await page.clock.install({ time: new Date('2026-11-06T02:00:00Z') })
        await mockAdmin(page, item.path !== '/login')
        await page.goto(item.path)
        await expect(
          page.getByRole('heading', { level: 1, name: item.heading, exact: true }),
        ).toBeVisible()
        if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
        await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
        if (item.path !== '/dev/components')
          await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
        ).toBe(true)
        await expect(page).toHaveScreenshot(
          `${item.path.replaceAll('/', '') || 'providers'}-${theme}-${width}.png`,
          { fullPage: true },
        )
      })
    }
  }
}
