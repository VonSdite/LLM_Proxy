import { expect, visualTest as test } from '../browser-test'
import { mockProviderTable } from '../provider-fixtures'

for (const width of [2560, 1920, 1440, 390]) {
  for (const theme of ['dark', 'light']) {
    test(`provider table ${theme} ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 1000 })
      await mockProviderTable(page)
      await page.goto('/')
      if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
      await page.getByRole('tab', { name: '全部', exact: true }).click()
      await page.getByRole('checkbox', { name: '选择 gateway', exact: true }).click()
      await expect(page.locator('tbody tr')).toHaveCount(5)
      await page.mouse.move(0, 0)
      await expect(page).toHaveScreenshot(`provider-table-${theme}-${width}.png`, {
        fullPage: true,
      })
    })
  }
}

for (const width of [1440, 390]) {
  for (const theme of ['dark', 'light']) {
    for (const scenario of [
      'models',
      'delete',
      'selection-delete',
      'tooltip',
      'selection-mixed',
      'selection-disabled',
      'drag-end',
    ]) {
      test(`provider ${scenario} ${theme} ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1000 })
        await mockProviderTable(page)
        await page.goto('/')
        if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
        if (scenario === 'drag-end') {
          const handle = (await page
            .getByRole('button', { name: '拖动 gateway 排序', exact: true })
            .boundingBox())!
          const last = (await page.locator('tbody tr').last().boundingBox())!
          await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2)
          await page.mouse.down()
          await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2 + 10)
          await page.mouse.move(handle.x + handle.width / 2, last.y + last.height + 12, {
            steps: 12,
          })
          await expect(page.locator('tbody tr').last()).toHaveAttribute(
            'data-drop-position',
            'after',
          )
          await expect(page.locator('.ui-table-wrap')).toHaveScreenshot(
            `provider-${scenario}-${theme}-${width}.png`,
          )
          await page.keyboard.press('Escape')
          await page.mouse.up()
          return
        }
        if (scenario === 'tooltip') {
          await page.getByRole('button', { name: '导出', exact: true }).hover()
          await expect(page.locator('.ui-tooltip .ui-popover-arrow')).toBeVisible()
          await expect(page).toHaveScreenshot(`provider-${scenario}-${theme}-${width}.png`, {
            fullPage: true,
          })
          return
        }
        if (scenario === 'selection-delete') {
          await page.getByRole('checkbox', { name: '选择 gateway', exact: true }).click()
          await page.getByRole('checkbox', { name: '选择 api_responses_tt', exact: true }).click()
          await page
            .getByRole('region', { name: '批量操作' })
            .getByRole('button', { name: '删除', exact: true })
            .click()
          await expect(page.locator('.ui-confirm .ui-popover-arrow')).toBeVisible()
          await page.mouse.move(0, 0)
          await expect(page).toHaveScreenshot(`provider-${scenario}-${theme}-${width}.png`, {
            fullPage: true,
          })
          return
        }
        if (scenario.startsWith('selection-')) {
          await page.getByRole('tab', { name: '全部', exact: true }).click()
          await page.getByRole('checkbox', { name: '选择 backup', exact: true }).click()
          if (scenario === 'selection-mixed') {
            await page.getByRole('checkbox', { name: '选择 gateway', exact: true }).click()
          }
          await page.mouse.move(0, 0)
          await expect(page.getByRole('region', { name: '批量操作' })).toHaveScreenshot(
            `provider-${scenario}-${theme}-${width}.png`,
          )
          return
        }
        if (scenario === 'models') {
          await page.getByRole('button', { name: '8 个模型', exact: true }).click()
          await expect(
            page.getByRole('dialog', { name: 'gateway · 模型', exact: true }),
          ).toBeVisible()
        } else {
          await page.getByRole('button', { name: '删除 gateway', exact: true }).click()
          await expect(page.locator('.ui-confirm p')).toHaveText('是否删除？')
          await expect(page.locator('.ui-confirm .ui-popover-arrow')).toBeVisible()
        }
        await page.mouse.move(0, 0)
        await expect(page).toHaveScreenshot(`provider-${scenario}-${theme}-${width}.png`, {
          fullPage: true,
        })
      })
    }
  }
}
