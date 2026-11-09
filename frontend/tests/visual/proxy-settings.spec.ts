import { expect, visualTest as test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

for (const width of [1440, 390]) {
  for (const theme of ['dark', 'light']) {
    for (const target of ['oauth', 'ssh']) {
      test(`shared proxy ${target} ${theme} ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1000 })
        const fixture = await mockAdmin(page)
        await page.goto(target === 'oauth' ? '/settings' : '/agents')
        if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
        if (target === 'ssh')
          await page.getByRole('button', { name: '添加 SSH 机器', exact: true }).click()
        const scope =
          target === 'oauth'
            ? page.getByRole('form', { name: 'OAuth 网络' })
            : page.getByRole('dialog', { name: '添加 SSH 机器' })
        await scope.getByRole('combobox', { name: '连接方式', exact: true }).click()
        await page.getByRole('option', { name: '自定义代理', exact: true }).click()
        await scope
          .getByRole('textbox', { name: '代理地址', exact: true })
          .fill('socks5h://proxy-user:p%40ss@127.0.0.1:1080')
        await expect(scope.getByRole('button', { name: '认证', exact: true })).toHaveAttribute(
          'aria-description',
          '已设置账号密码',
        )
        await scope.getByRole('button', { name: '认证', exact: true }).click()
        const popup = page.getByRole('dialog', { name: '代理认证', exact: true })
        await expect(popup.getByRole('textbox', { name: '代理用户名', exact: true })).toHaveValue(
          'proxy-user',
        )
        await expect(popup.getByRole('textbox', { name: '代理密码', exact: true })).toHaveValue(
          'p@ss',
        )
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        )
        await page.mouse.move(0, 0)
        const box = (await popup.boundingBox())!
        expect(box.x).toBeGreaterThanOrEqual(0)
        expect(box.x + box.width).toBeLessThanOrEqual(width)
        await expect(page).toHaveScreenshot(`proxy-${target}-${theme}-${width}.png`)
        expect(fixture.unexpected).toEqual([])
      })
    }
  }
}
