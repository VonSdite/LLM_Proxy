import { expect, visualTest as test } from '../browser-test'
import { mockProviderTable } from '../provider-fixtures'

for (const theme of ['dark', 'light']) {
  for (const width of [1440, 390]) {
    for (const scenario of ['provider-import', 'group-yaml', 'mapping-import', 'group-import']) {
      test(`code-editor ${scenario} ${theme} ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1000 })
        const fixture = await mockProviderTable(page)
        await page.goto(scenario === 'mapping-import' ? '/model-mappings' : '/')
        if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
        if (scenario.startsWith('group-'))
          await page.getByRole('tab', { name: /Auth Groups/ }).click()
        if (scenario === 'group-yaml') {
          await page.getByRole('button', { name: '编辑 Auth Group team-pool', exact: true }).click()
          await page.getByRole('button', { name: '编辑 / 导入', exact: true }).click()
        } else {
          await page.getByRole('button', { name: '导入', exact: true }).click()
          await page
            .getByRole('dialog')
            .locator('input[type=file]')
            .setInputFiles({
              name: 'example.json',
              mimeType: 'application/json',
              buffer: Buffer.from(
                JSON.stringify(
                  scenario === 'provider-import'
                    ? { version: 1, providers: [fixture.provider] }
                    : scenario === 'mapping-import'
                      ? {
                          version: 1,
                          model_mappings: [
                            {
                              id: 'smart',
                              strategy: 'highest_priority',
                              targets: [
                                { model_id: 'main/demo-model', priority: 1, enabled: true },
                              ],
                            },
                          ],
                        }
                      : {
                          version: 1,
                          auth_groups: [
                            {
                              name: 'team-pool',
                              strategy: 'least_inflight',
                              entries: [
                                {
                                  id: 'key-a',
                                  headers: { Authorization: 'Bearer example-key' },
                                  max_concurrency: 4,
                                },
                              ],
                            },
                          ],
                        },
                  null,
                  2,
                ),
              ),
            })
        }
        const editor = page.locator('.ui-code-editor')
        await expect(editor.locator('.ace_line').first()).toBeVisible()
        await expect(editor).toHaveCSS('font-size', '15px')
        const gutter = (await editor.locator('.ace_gutter').boundingBox())!
        const content = (await editor.locator('.ace_scroller').boundingBox())!
        expect(gutter.width).toBeLessThan(42)
        expect(content.x).toBeGreaterThanOrEqual(gutter.x + gutter.width - 1)
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        )
        await page.mouse.move(0, 0)
        await expect(page).toHaveScreenshot(`code-editor-${scenario}-${theme}-${width}.png`, {
          fullPage: true,
        })
      })
    }
  }
}
