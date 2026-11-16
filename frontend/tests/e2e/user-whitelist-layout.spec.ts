import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

for (const enabled of [false, true]) {
  test(`白名单 ${enabled}：条件列、标题旁开关及单项批量权限入口`, async ({ page }) => {
    const fixture = await mockAdmin(page)
    fixture.bootstrap.features.whitelist = enabled
    await page.goto('/users')
    await expect(page.getByRole('columnheader', { name: /^白名单/ })).toHaveCount(enabled ? 1 : 0)
    const toggle = page.getByRole('switch', { name: '启用白名单', exact: true })
    const search = page.getByRole('textbox', { name: '搜索用户' })
    await expect(toggle).toBeVisible()
    expect((await toggle.boundingBox())!.x).toBeLessThan((await search.boundingBox())!.x)
    await page
      .locator('tbody')
      .getByRole('button', { name: '全部模型', exact: true })
      .first()
      .click()
    const dialog = page.getByRole('dialog', { name: '编辑用户', exact: true })
    await expect(dialog.getByRole('tab', { name: '指定模型', exact: true })).toBeEnabled()
    await expect(dialog.getByRole('switch', { name: '允许白名单访问' })).toHaveCount(0)
    await dialog.getByRole('button', { name: '取消', exact: true }).click()
    await page.getByRole('checkbox', { name: '选择当前列表', exact: true }).click()
    await page
      .getByRole('region', { name: '批量操作' })
      .getByRole('button', { name: '模型权限', exact: true })
      .click()
    await expect(page.getByRole('dialog', { name: /个用户的模型权限/ })).toBeVisible()
  })
}
