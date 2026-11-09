import { expect, visualTest as test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

for (const width of [1440, 390]) {
  for (const theme of ['dark', 'light']) {
    test(`unsaved-dialog ${theme} ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 1000 })
      await mockAdmin(page)
      await page.goto('/')
      if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
      await page.getByRole('button', { name: '编辑 Provider main', exact: true }).click()
      const editor = page.getByRole('dialog', { name: '编辑 Provider', exact: true })
      await editor.getByRole('textbox', { name: 'Provider 名称', exact: true }).fill('changed')
      await page.mouse.click(4, 80)
      const prompt = page.getByRole('dialog', { name: '保存修改？', exact: true })
      await expect(prompt).toBeVisible()
      const box = (await prompt.boundingBox())!
      expect(box.x).toBeGreaterThanOrEqual(0)
      expect(box.x + box.width).toBeLessThanOrEqual(width)
      await expect(page).toHaveScreenshot(`unsaved-dialog-${theme}-${width}.png`)
      await page.mouse.click(4, 80)
      await expect(prompt).toHaveCount(0)
      await expect(editor.getByRole('textbox', { name: 'Provider 名称', exact: true })).toHaveValue(
        'changed',
      )
    })
  }
}
