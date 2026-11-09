import { expect, test } from '../browser-test'
import { mockAdmin, pages } from '../browser-fixtures'

test('登录错误保留表单，正确登录后展示统一外壳', async ({ page }) => {
  const fixture = await mockAdmin(page, false)
  await page.goto('/login')
  await page.getByLabel('用户名', { exact: true }).fill('admin')
  await page.getByLabel('密码', { exact: true }).fill('wrong')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('账号或密码不正确')
  await page.getByLabel('密码', { exact: true }).fill('correct-password')
  await page.getByLabel('密码', { exact: true }).press('Enter')
  await expect(page.getByRole('heading', { name: 'Provider', exact: true })).toBeVisible()
  expect(fixture.unexpected).toEqual([])
})

for (const item of pages) {
  test(`${item.path} 支持深链接、刷新和主题切换`, async ({ page }) => {
    const fixture = await mockAdmin(page)
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.goto(item.path)
    await expect(
      page.getByRole('heading', { level: 1, name: item.heading, exact: true }),
    ).toBeVisible()
    await page.getByRole('button', { name: '切换主题' }).click()
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
    await page.reload()
    await expect(
      page.getByRole('heading', { level: 1, name: item.heading, exact: true }),
    ).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
    expect(errors).toEqual([])
    expect(fixture.unexpected).toEqual([])
  })
}

test('Provider 模型输入保持焦点和 DOM，确认放弃后焦点回到入口', async ({ page }) => {
  await mockAdmin(page)
  await page.goto('/')
  const trigger = page.getByRole('button', { name: '新增 Provider' })
  await trigger.click()
  const dialog = page.getByRole('dialog', { name: '新增 Provider', exact: true })
  await dialog.getByRole('button', { name: '添加', exact: true }).click()
  const input = dialog.getByRole('textbox', { name: '模型 1' })
  await input.fill('org/model-with-full-id')
  await expect(input).toBeFocused()
  await expect(input).toHaveValue('org/model-with-full-id')
  await page.keyboard.press('Escape')
  const prompt = page.getByRole('dialog', { name: '保存修改？', exact: true })
  await expect(prompt).toBeVisible()
  await prompt.getByRole('button', { name: '不保存', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  await expect(trigger).toBeFocused()
})

test('Agent 外部地址一键模型同步，预览刷新保持焦点，文件脏关闭确认', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/agents')
  await page.getByLabel('API 地址', { exact: true }).fill('https://external.example/v1')
  const all = page.getByLabel('一键设置模型', { exact: true })
  await all.fill('external-model')
  await expect(page.getByLabel('ANTHROPIC_MODEL', { exact: true })).toHaveValue('external-model')
  await expect(page.getByLabel('ANTHROPIC_DEFAULT_OPUS_MODEL', { exact: true })).toHaveValue(
    'external-model',
  )
  await expect(page.getByText('有待保存的修改')).toBeVisible()
  await expect(all).toBeFocused()
  await page.getByRole('button', { name: fixture.snapshot.files.claude.path, exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.locator('.ace_editor')).toBeVisible()
  await dialog.locator('.ace_text-input').focus()
  await page.keyboard.press('ControlOrMeta+A')
  await page.keyboard.insertText('{"model":"edited"}')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: '保存修改？' })).toBeVisible()
  await page.getByRole('button', { name: '继续编辑' }).click()
  await expect(page.getByRole('dialog', { name: '保存修改？' })).not.toBeVisible()
  expect(fixture.calls.filter((call) => call.path.endsWith('/apply'))).toEqual([])
})

test('统计默认今天、按需请求日志、页码跳转与导出范围', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/statistics')
  await expect(page.getByText('开发用户').first()).toBeVisible()
  expect(fixture.calls.some((call) => call.path === '/api/request-logs')).toBe(false)
  await page.getByRole('tab', { name: '请求明细' }).click()
  const jump = page.getByRole('textbox', { name: '跳转页码' })
  await jump.fill('2')
  await jump.press('Tab')
  await expect
    .poll(() => fixture.calls.filter((call) => call.path === '/api/request-logs').at(-1)?.url)
    .toContain('page=2')
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出 Excel' }).click()
  expect((await download).suggestedFilename()).toBe('statistics.xlsx')
  const url = new URL(
    fixture.calls.filter((call) => call.path === '/api/statistics/export').at(-1)!.url,
  )
  expect(url.searchParams.get('tab')).toBe('logs')
  expect(url.searchParams.has('page')).toBe(false)
})

test('关闭功能后隐藏导航并阻止直接进入', async ({ page }) => {
  const fixture = await mockAdmin(page)
  fixture.bootstrap.features.api_keys = false
  await page.goto('/api-keys')
  await expect(page).toHaveURL(/\/settings$/)
  await expect(
    page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: 'API Key' }),
  ).toHaveCount(0)
})
