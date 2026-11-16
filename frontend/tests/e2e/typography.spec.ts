import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'
import { mockOAuthCards } from '../oauth-fixtures'

for (const theme of ['dark', 'light']) {
  test(`列表名称与 Provider 字体一致 ${theme}`, async ({ page }) => {
    await mockAdmin(page)
    await mockOAuthCards(page)
    await page.goto('/')
    if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
    const provider = page.locator('.provider-name').first()
    await expect(provider).toBeVisible()
    const baseline = await provider.evaluate((element) => {
      const style = getComputedStyle(element)
      return { family: style.fontFamily, size: style.fontSize, weight: style.fontWeight }
    })
    for (const [path, selector] of [
      ['/model-mappings', '.ui-button--title'],
      ['/users', '.record-title'],
      ['/api-keys', '.key-name'],
      ['/oauth', '.record-title'],
    ]) {
      await page.goto(path!)
      const title = page.locator(selector!).first()
      await expect(title).toBeVisible()
      await expect(title).toHaveCSS('font-family', baseline.family)
      await expect(title).toHaveCSS('font-size', baseline.size)
      await expect(title).toHaveCSS('font-weight', baseline.weight)
      if (path === '/api-keys' || path === '/users') {
        const models = page.getByRole('button', { name: /^(全部模型|\d+ 个模型)$/ }).first()
        await expect(models).toHaveCSS('font-size', '14px')
        await expect(models).toHaveCSS('font-family', baseline.family)
        await expect(models).toHaveClass(/ui-button--link/)
      }
      if (path === '/api-keys') {
        await expect(page.locator('.key-text').first()).toHaveCSS('font-size', '14px')
        await expect(page.locator('.key-request-count').first()).toHaveCSS('font-size', '13px')
      }
    }
  })
}
