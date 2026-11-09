import { expect, visualTest as test } from '../browser-test'
import { mockOAuthCards } from '../oauth-fixtures'

for (const width of [1440, 390]) {
  for (const theme of ['dark', 'light']) {
    test(`oauth-login ${theme} ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 1000 })
      await mockOAuthCards(page)
      await page.route('**/api/oauth/codex/session', (route) =>
        route.fulfill({
          json: {
            authorization_url:
              'https://auth.example/authorize?state=fixture&client_id=codex&redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback',
          },
        }),
      )
      await page.goto('/oauth')
      if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
      await page.getByRole('button', { name: '添加账号', exact: true }).click()
      const dialog = page.getByRole('dialog', { name: '添加 Codex 账号' })
      await dialog.getByRole('button', { name: '生成授权链接' }).click()
      await expect(dialog.getByRole('link', { name: '打开链接' })).toBeVisible()
      await expect(page).toHaveScreenshot(`oauth-login-${theme}-${width}.png`)
    })
    for (const filter of ['available', 'all']) {
      test(`oauth-cards ${filter} ${theme} ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1000 })
        await page.clock.install({ time: new Date('2026-11-07T02:00:00Z') })
        await mockOAuthCards(page)
        await page.goto('/oauth')
        await expect(page.getByRole('article')).toHaveCount(2)
        if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
        if (filter === 'all') await page.getByRole('tab', { name: '全部 6', exact: true }).click()
        await expect(page.getByRole('article')).toHaveCount(filter === 'all' ? 6 : 2)
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        )
        await expect(page).toHaveScreenshot(`oauth-cards-${filter}-${theme}-${width}.png`, {
          fullPage: true,
        })
      })
    }
  }
}
