import { expect, visualTest as test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'
import { mockOAuthCards } from '../oauth-fixtures'
import { mockProviderTable } from '../provider-fixtures'

for (const width of [1440, 390]) {
  for (const theme of ['dark', 'light']) {
    for (const scenario of [
      'provider-actions',
      'provider-selection',
      'auth-groups',
      'auth-group-selection',
      'oauth-selection',
      'mapping-selection',
      'users-selection',
      'model-selection',
    ]) {
      test(`list-toolbar ${scenario} ${theme} ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1000 })
        await page.clock.install({ time: new Date('2026-11-07T02:00:00Z') })
        if (scenario === 'oauth-selection') await mockOAuthCards(page)
        else if (scenario === 'auth-groups' || scenario === 'auth-group-selection')
          await mockProviderTable(page)
        else await mockAdmin(page)
        await page.goto(
          scenario === 'oauth-selection'
            ? '/oauth'
            : scenario === 'mapping-selection'
              ? '/model-mappings'
              : scenario === 'users-selection'
                ? '/users'
                : '/',
        )
        if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
        if (scenario === 'provider-actions') {
          await expect(page.getByRole('button', { name: '导入', exact: true })).toBeVisible()
          await expect(page.getByRole('button', { name: '导出', exact: true })).toBeVisible()
        } else if (scenario === 'auth-groups' || scenario === 'auth-group-selection') {
          await page.getByRole('tab', { name: /Auth Groups/ }).click()
          await expect(
            page.getByRole('button', { name: '新增 Auth Group', exact: true }),
          ).toBeVisible()
          if (scenario === 'auth-group-selection') {
            await page.getByRole('checkbox', { name: '选择 team-pool', exact: true }).click()
            await expect(page.getByRole('region', { name: '批量操作' })).toBeVisible()
          }
        } else if (scenario === 'model-selection') {
          await page.getByRole('button', { name: '编辑 Provider main', exact: true }).click()
          await page
            .getByRole('dialog')
            .getByRole('checkbox', { name: '选择 1', exact: true })
            .click()
          await expect(page.getByRole('region', { name: '批量操作' })).toBeVisible()
        } else {
          await page
            .getByRole('checkbox', {
              name:
                scenario === 'oauth-selection'
                  ? '选择当前筛选的账号'
                  : scenario === 'mapping-selection'
                    ? '选择 smart'
                    : scenario === 'users-selection'
                      ? '选择 1'
                      : '选择 main',
              exact: true,
            })
            .click()
          await expect(page.getByRole('region', { name: '批量操作' })).toBeVisible()
        }
        await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        )
        await page.mouse.move(0, 0)
        await expect(page).toHaveScreenshot(`list-toolbar-${scenario}-${theme}-${width}.png`, {
          fullPage: true,
        })
      })
    }
  }
}
