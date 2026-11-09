import { expect, visualTest as test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

for (const width of [2560, 1440, 390]) {
  for (const theme of ['dark', 'light']) {
    for (const [name, label] of [
      ['access', '客户端 IP'],
      ['oauth', 'OAuth 网络'],
      ['debug', '日志与调试'],
    ]) {
      test(`settings-${name} ${theme} ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1000 })
        const fixture = await mockAdmin(page)
        fixture.settings.oauth.proxy_mode = 'custom'
        fixture.settings.oauth.proxy = 'socks5://127.0.0.1:1080'
        await page.goto('/settings')
        await expect(page.locator('.settings-panel')).toBeVisible()
        if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
        const section = page.getByRole('form', { name: label, exact: true })
        await expect(section).toBeVisible()
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        )
        await expect(section).toHaveScreenshot(`settings-${name}-${theme}-${width}.png`)
        expect(fixture.unexpected).toEqual([])
      })
    }
  }
}
