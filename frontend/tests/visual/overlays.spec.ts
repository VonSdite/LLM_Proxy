import { expect, visualTest as test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

const scenarios = [
  'toast',
  'ssh',
  'api-key-menu',
  'model-menu',
  'parameter-menu',
  'help',
  'toml-editor',
  'jsonc-editor',
  'provider-dialog',
  'mapping-dialog',
  'key-dialog',
  'auth-group-dialog',
  'statistics-filter',
] as const

for (const width of [1440, 390]) {
  for (const theme of ['dark', 'light']) {
    for (const scenario of scenarios) {
      test(`${scenario} ${theme} ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 1000 })
        await page.clock.install({ time: new Date('2026-11-06T02:00:00Z') })
        const fixture = await mockAdmin(page)
        fixture.snapshot.files.opencode.path = 'D:/配置目录/项目配置/opencode.jsonc'
        const path =
          scenario === 'toast'
            ? '/dev/components'
            : scenario === 'provider-dialog' || scenario === 'auth-group-dialog'
              ? '/'
              : scenario === 'mapping-dialog'
                ? '/model-mappings'
                : scenario === 'key-dialog'
                  ? '/api-keys'
                  : scenario === 'statistics-filter'
                    ? '/statistics'
                    : '/agents'
        await page.goto(path)
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
        if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
        if (scenario === 'toast') {
          await page.getByRole('button', { name: '显示通知', exact: true }).click()
          await expect(page.locator('.ui-toast')).toContainText('配置已保存')
        } else if (scenario === 'ssh') {
          await page.getByRole('button', { name: '添加 SSH 机器', exact: true }).click()
          const dialog = page.getByRole('dialog')
          await dialog.getByRole('combobox', { name: '连接方式' }).click()
          await page.getByRole('option', { name: '自定义代理' }).click()
          await dialog.getByRole('button', { name: '保存', exact: true }).click()
          await expect(dialog.getByRole('textbox', { name: 'IP / 主机名' })).toHaveAttribute(
            'aria-invalid',
            'true',
          )
        } else if (scenario === 'api-key-menu') {
          await page.getByRole('combobox', { name: 'API Key', exact: true }).click()
          await expect(page.getByRole('option', { name: /开发 Key/ })).toBeVisible()
        } else if (scenario === 'model-menu') {
          await page.getByRole('combobox', { name: '一键设置模型', exact: true }).click()
          await expect(page.getByRole('listbox')).toBeVisible()
        } else if (scenario === 'parameter-menu') {
          await page
            .locator('.ui-field')
            .filter({ has: page.getByRole('textbox', { name: '压缩窗口', exact: true }) })
            .getByRole('button', { name: '参数参考值' })
            .click()
          await expect(page.locator('.ui-number-options')).toBeVisible()
        } else if (scenario === 'help') {
          await page.getByRole('button', { name: 'ANTHROPIC_MODEL说明', exact: true }).hover()
          await expect(page.locator('.ui-help-content')).toBeVisible()
        } else if (scenario === 'toml-editor' || scenario === 'jsonc-editor') {
          const target = scenario === 'toml-editor' ? 'codex' : 'opencode'
          await page
            .getByRole('tab', { name: target === 'codex' ? 'Codex' : 'opencode', exact: true })
            .click()
          await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
          await page
            .getByRole('button', { name: fixture.snapshot.files[target].path, exact: true })
            .click()
          await expect(page.getByRole('dialog').locator('.ace_string').first()).toBeVisible()
          await expect(
            page.getByRole('dialog').locator(theme === 'dark' ? '.ace-tomorrow-night' : '.ace-tm'),
          ).toBeVisible()
        } else if (scenario === 'provider-dialog') {
          await page.getByRole('button', { name: '编辑 main', exact: true }).click()
          await page
            .getByRole('dialog')
            .getByRole('combobox', { name: '上游格式', exact: true })
            .click()
          await expect(page.getByRole('listbox')).toBeVisible()
        } else if (scenario === 'mapping-dialog') {
          await page.getByRole('button', { name: '创建映射', exact: true }).click()
          await page
            .getByRole('dialog')
            .getByRole('combobox', { name: '目标 1', exact: true })
            .click()
          await expect(page.getByRole('listbox')).toBeVisible()
        } else if (scenario === 'key-dialog') {
          await page.getByRole('button', { name: '创建 API Key', exact: true }).click()
          await page.getByRole('dialog').getByRole('tab', { name: '指定模型' }).click()
          await expect(
            page.getByRole('checkbox', { name: 'main/claude-sonnet-4-6', exact: true }),
          ).toBeVisible()
        } else if (scenario === 'auth-group-dialog') {
          await page.getByRole('tab', { name: /Auth Groups/ }).click()
          await page.getByRole('button', { name: '新增 Auth Group' }).click()
          await expect(
            page.getByRole('dialog').getByRole('textbox', { name: 'Entry ID', exact: true }),
          ).toBeVisible()
        } else {
          await page.getByRole('button', { name: '请求模型', exact: true }).click()
          await expect(page.getByRole('textbox', { name: '搜索选项' })).toBeVisible()
        }
        if (scenario !== 'toast') await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
        ).toBe(true)
        const overlays = page.locator(
          '.ui-select-content, .ui-combobox-content, .ui-help-content, .ui-number-options, .ui-multi-content',
        )
        for (const overlay of await overlays.all()) {
          const box = (await overlay.boundingBox())!
          expect(box.x).toBeGreaterThanOrEqual(0)
          expect(box.x + box.width).toBeLessThanOrEqual(width)
          await expect(overlay).toHaveCSS('z-index', '60')
          await expect(overlay).not.toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')
        }
        await expect(page).toHaveScreenshot(`${scenario}-${theme}-${width}.png`)
        expect(fixture.unexpected).toEqual([])
      })
    }
  }
}
