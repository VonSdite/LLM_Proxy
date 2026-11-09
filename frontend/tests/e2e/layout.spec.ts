import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

test('站内前进后退保持页面与主题，重复进入只挂载一份页面', async ({ page }) => {
  await mockAdmin(page)
  await page.goto('/')
  await page.getByRole('button', { name: '切换主题' }).click()
  const nav = page.getByRole('navigation', { name: '主导航' })
  await nav.getByRole('link', { name: '用户', exact: true }).click()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('用户')
  await nav.getByRole('link', { name: '设置', exact: true }).click()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('系统设置')
  await page.goBack()
  await expect(page).toHaveURL(/\/users$/)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('用户')
  await page.goForward()
  await expect(page).toHaveURL(/\/settings$/)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('系统设置')
  await page.goBack()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('用户')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
})

test('用户表覆盖加载、错误重试、空数据与千条数据的分页选择', async ({ page }) => {
  await mockAdmin(page)
  let release!: () => void
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  let state: 'loading' | 'empty' | 'populated' = 'loading'
  await page.route('**/api/users?*', async (route) => {
    if (state === 'loading') {
      await gate
      await route.fulfill({ status: 503, json: { error: 'Service temporarily unavailable' } })
      return
    }
    const query = new URL(route.request().url()).searchParams
    const size = Number(query.get('page_size'))
    const index = Number(query.get('page'))
    const filtered = !!query.get('keyword')
    const total = state === 'empty' ? 0 : filtered ? 1 : 1000
    const start = filtered ? 999 : (index - 1) * size
    const users = Array.from({ length: Math.min(size, total) }, (_, offset) => ({
      id: start + offset + 1,
      username: `user-${start + offset + 1}`,
      ip_address: '192.168.1.10',
      whitelist_access_enabled: true,
      model_permissions: '*',
      allowed_models_count: 2,
      created_at: '2026-11-06 10:00:00',
    }))
    await route.fulfill({ json: { users, total, available_models: [] } })
  })
  await page.goto('/users')
  await expect(page.getByRole('status')).toContainText('正在加载')
  release()
  await expect(page.getByRole('alert')).toContainText('Service temporarily unavailable')
  state = 'empty'
  await page.getByRole('button', { name: '刷新用户' }).click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect(page.getByText('还没有用户', { exact: true })).toBeVisible()
  state = 'populated'
  await page.getByRole('button', { name: '刷新用户' }).click()
  await expect(page.getByRole('cell', { name: 'user-50', exact: true })).toBeVisible()
  await expect(page.locator('tbody tr')).toHaveCount(50)
  await page.getByRole('checkbox', { name: '选择当前列表' }).click()
  await expect(page.getByRole('region', { name: '批量操作' })).toContainText('已选 50 项')
  await page.getByRole('textbox', { name: '跳转页码' }).fill('20')
  await page.getByRole('textbox', { name: '跳转页码' }).press('Enter')
  await expect(page.getByRole('cell', { name: 'user-1000', exact: true })).toBeVisible()
  await expect(page.getByRole('checkbox', { name: '选择当前列表' })).not.toBeChecked()
  await page.getByRole('textbox', { name: '搜索用户' }).fill('user-1000')
  await expect(page.locator('tbody tr')).toHaveCount(1)
  await expect(page.getByRole('textbox', { name: '跳转页码' })).toHaveValue('1')
})

test('通知固定在视口内且不会增加页面高度，深浅主题共用样式', async ({ page }) => {
  await mockAdmin(page)
  await page.setViewportSize({ width: 390, height: 1000 })
  await page.goto('/dev/components')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('组件展示')
  for (const theme of ['dark', 'light']) {
    if (theme === 'light') await page.getByRole('button', { name: '切换主题' }).click()
    const before = await page.evaluate(() => document.documentElement.scrollHeight)
    await page.getByRole('button', { name: '显示通知' }).click()
    const toast = page.locator('.ui-toast')
    await expect(toast).toContainText('配置已保存')
    await expect(page.locator('.ui-toast-viewport')).toHaveCSS('position', 'fixed')
    await expect(toast).not.toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')
    const box = (await toast.boundingBox())!
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(390)
    expect(box.y + box.height).toBeLessThanOrEqual(1000)
    expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBe(before)
    await page.getByRole('button', { name: '关闭通知' }).click()
    await expect(toast).toHaveCount(0)
  }
})

test.describe('触屏', () => {
  test.use({ hasTouch: true, viewport: { width: 390, height: 1000 } })
  test('公共控件提供至少 44px 点击区域并支持触摸操作', async ({ page }) => {
    await mockAdmin(page)
    await page.goto('/dev/components')
    expect(await page.evaluate(() => matchMedia('(pointer: coarse)').matches)).toBe(true)
    const toggle = page.getByRole('switch', { name: '功能开关' })
    const checkbox = page.getByRole('checkbox', { name: '选择项目' })
    const help = page.getByRole('button', { name: '模型 ID说明', exact: true })
    const select = page.getByRole('combobox', { name: '固定选项', exact: true })
    for (const control of [
      toggle,
      checkbox,
      help,
      select,
      page.getByRole('button', { name: '切换主题' }),
    ]) {
      const box = (await control.boundingBox())!
      expect(box.width).toBeGreaterThanOrEqual(44)
      expect(box.height).toBeGreaterThanOrEqual(44)
    }
    await toggle.tap()
    await expect(toggle).not.toBeChecked()
    await checkbox.tap()
    await expect(checkbox).toBeChecked()
    await help.tap()
    await expect(page.locator('.ui-help-content')).toBeVisible()
    await page.getByRole('heading', { level: 1 }).tap()
    await select.tap()
    await page.getByRole('option', { name: /Codex/ }).tap()
    await expect(select).toHaveText('Codex')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.goto('/')
    await expect(page.getByRole('button', { name: /^(上移|下移) / })).toHaveCount(0)
    for (const label of ['拖动 main 排序']) {
      const control = page.getByRole('button', { name: label, exact: true })
      const box = (await control.boundingBox())!
      const cell = (await control.locator('xpath=ancestor::td').boundingBox())!
      expect(box.width).toBeGreaterThanOrEqual(44)
      expect(box.height).toBeGreaterThanOrEqual(44)
      expect(box.x).toBeGreaterThanOrEqual(cell.x)
      expect(box.x + box.width).toBeLessThanOrEqual(cell.x + cell.width)
    }
  })
})
