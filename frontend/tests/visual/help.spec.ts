import { expect, visualTest as test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

const scenarios = [
  { name: 'claude-permissions', path: '/agents', label: '权限模式说明' },
  { name: 'claude-context', path: '/agents', label: '压缩窗口说明' },
  { name: 'claude-attribution', path: '/agents', label: 'Attribution说明' },
  { name: 'proxy', path: '/', label: '连接方式说明', dialog: true },
  { name: 'proxy-address', path: '/', label: '代理地址说明', dialog: true, proxy: true },
  { name: 'redaction', path: '/', label: '安全脱敏说明', dialog: true },
  { name: 'model-ttft', path: '/', label: '首字延迟说明', dialog: true },
  { name: 'model-tps', path: '/', label: 'TPS说明', dialog: true },
  { name: 'table-header', path: '/statistics', label: '缓存命中率说明' },
  { name: 'oauth-status', path: '/oauth', label: '认证文件与额度说明' },
] as const

for (const width of [1440, 390]) {
  for (const theme of ['dark', 'light']) {
    for (const scenario of scenarios) {
      test(`${scenario.name} ${theme} ${width}px`, async ({ page }) => {
        const height = width === 390 ? 600 : 1000
        await page.setViewportSize({ width, height })
        await page.clock.install({ time: new Date('2026-11-06T02:00:00Z') })
        const fixture = await mockAdmin(page)
        await page.goto(scenario.path)
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
        if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
        if ('dialog' in scenario)
          await page.getByRole('button', { name: '编辑 Provider main', exact: true }).click()
        if ('proxy' in scenario) {
          await page.getByRole('combobox', { name: '连接方式', exact: true }).click()
          await page.getByRole('option', { name: '自定义代理', exact: true }).click()
        }
        await page.getByRole('button', { name: scenario.label, exact: true }).hover()
        const content = page.locator('.ui-help-content')
        await expect(content).toBeVisible()
        await expect(content.locator('.ui-popover-arrow')).toBeVisible()
        const box = (await content.boundingBox())!
        expect(box.x).toBeGreaterThanOrEqual(0)
        expect(box.y).toBeGreaterThanOrEqual(0)
        expect(box.x + box.width).toBeLessThanOrEqual(width)
        expect(box.y + box.height).toBeLessThanOrEqual(height)
        await expect(content).not.toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')
        await expect(content).toHaveCSS('z-index', '60')
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        )
        await expect(page).toHaveScreenshot(`${scenario.name}-${theme}-${width}.png`)
        expect(fixture.unexpected).toEqual([])
      })
    }
  }
}
