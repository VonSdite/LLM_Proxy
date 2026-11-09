import type { Locator, Page } from '@playwright/test'
import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

async function inspectHelp(page: Page, scope: Locator = page.locator('main')) {
  const buttons = scope.locator('.ui-help:visible')
  await expect(buttons.first()).toBeVisible()
  await expect(scope.locator('[aria-busy="true"]')).toHaveCount(0)
  for (const button of await buttons.all()) {
    const label = await button.getAttribute('aria-label')
    expect(label).toBeTruthy()
    expect(label).not.toBe('查看说明')
    await button.hover()
    const content = page.locator('.ui-help-content')
    await expect(content, label!).toBeVisible()
    expect((await content.innerText()).trim().length, label!).toBeGreaterThan(15)
    expect(await button.getAttribute('aria-describedby')).toBe(await content.getAttribute('id'))
    const box = (await content.boundingBox())!
    const viewport = page.viewportSize()!
    expect(box.x, label!).toBeGreaterThanOrEqual(0)
    expect(box.y, label!).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width, label!).toBeLessThanOrEqual(viewport.width)
    expect(box.y + box.height, label!).toBeLessThanOrEqual(viewport.height)
    await page.keyboard.press('Escape')
    await expect(content).toHaveCount(0)
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
}

for (const path of ['/users', '/api-keys', '/model-mappings', '/oauth', '/statistics']) {
  test(`${path} 的页面说明可以悬停阅读且不溢出`, async ({ page }) => {
    const fixture = await mockAdmin(page)
    await page.goto(path)
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
    await inspectHelp(page)
    expect(fixture.unexpected).toEqual([])
  })
}

test('三个 Agent 页签、文件编辑器和自定义路径的说明可读', async ({ page }) => {
  test.setTimeout(60_000)
  const fixture = await mockAdmin(page)
  await page.goto('/agents')
  for (const agent of ['Claude', 'Codex', 'opencode']) {
    await page.getByRole('tab', { name: agent, exact: true }).click()
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
    await inspectHelp(page)
  }
  await page
    .getByRole('button', { name: fixture.snapshot.files.opencode.path, exact: true })
    .click()
  await inspectHelp(page, page.getByRole('dialog'))
  await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).first().click()
  await page.getByRole('button', { name: 'opencode.json 路径设置', exact: true }).click()
  await inspectHelp(page, page.getByRole('dialog'))
  expect(fixture.unexpected).toEqual([])
})

test('系统设置所有分组及代理字段的说明可读', async ({ page }) => {
  test.setTimeout(60_000)
  const fixture = await mockAdmin(page)
  await page.goto('/settings')
  await page.getByRole('combobox', { name: '连接方式' }).click()
  await page.getByRole('option', { name: '自定义代理' }).click()
  await expect(page.getByRole('form')).toHaveCount(6)
  for (const section of await page.getByRole('form').all()) await inspectHelp(page, section)
  expect(fixture.unexpected).toEqual([])
})

const dialogs = [
  { path: '/', button: '编辑 Provider main' },
  { path: '/users', button: '添加用户' },
  { path: '/api-keys', button: '创建 API Key' },
  { path: '/model-mappings', button: '创建映射' },
  { path: '/oauth', button: '模型目录' },
  { path: '/oauth', button: '添加账号' },
  { path: '/agents', button: '添加 SSH 机器' },
] as const
for (const scenario of dialogs) {
  test(`${scenario.button} 弹窗说明可读`, async ({ page }) => {
    const fixture = await mockAdmin(page)
    await page.goto(scenario.path)
    await page.getByRole('button', { name: scenario.button, exact: true }).click()
    const dialog = page.getByRole('dialog')
    if (scenario.path === '/' || scenario.path === '/agents') {
      await dialog.getByRole('combobox', { name: '连接方式' }).click()
      await page.getByRole('option', { name: '自定义代理' }).click()
    }
    await inspectHelp(page, dialog)
    expect(fixture.unexpected).toEqual([])
  })
}

test('认证组的调度、限制和请求头说明可读', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/')
  await page.getByRole('tab', { name: /Auth Groups/ }).click()
  await inspectHelp(page)
  await page.getByRole('button', { name: '新增 Auth Group', exact: true }).click()
  await inspectHelp(page, page.getByRole('dialog'))
  expect(fixture.unexpected).toEqual([])
})

test('认证运行状态的统计口径与重置影响说明可读', async ({ page }) => {
  const fixture = await mockAdmin(page)
  const summary = { entry_count: 1, available_count: 1, disabled_count: 0, cooldown_count: 0 }
  await page.route('**/api/auth-groups', (route) =>
    route.fulfill({
      json: [{ name: 'pool', strategy: 'least_inflight', entries: [{ id: 'key-a' }], summary }],
    }),
  )
  await page.route('**/api/auth-groups/pool/runtime', (route) =>
    route.fulfill({
      json: {
        name: 'pool',
        summary,
        entries: [
          { id: 'key-a', enabled: true, disabled: false, status: 'available', inflight: 0 },
        ],
      },
    }),
  )
  await page.goto('/')
  await page.getByRole('tab', { name: /Auth Groups/ }).click()
  await inspectHelp(page)
  await page.getByRole('button', { name: '运行状态', exact: true }).click()
  await inspectHelp(page, page.getByRole('dialog'))
  expect(fixture.unexpected).toEqual([])
})

test('批量模型权限说明在提交前解释替换范围', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/users')
  await page.getByRole('checkbox', { name: '选择当前列表', exact: true }).click()
  await page
    .getByRole('region', { name: '批量操作' })
    .getByRole('button', { name: '模型权限', exact: true })
    .click()
  await inspectHelp(page, page.getByRole('dialog'))
  expect(fixture.unexpected).toEqual([])
})

for (const path of ['/', '/users', '/model-mappings', '/statistics']) {
  test(`${path} 导入弹窗提供对应的覆盖或合并规则`, async ({ page }) => {
    const fixture = await mockAdmin(page)
    await page.goto(path)
    if (path === '/statistics')
      await page.getByRole('button', { name: '导入数据', exact: true }).click()
    else await page.getByRole('button', { name: '导入', exact: true }).click()
    await inspectHelp(page, page.getByRole('dialog'))
    expect(fixture.unexpected).toEqual([])
  })
}

test('长说明在短窗口可滚动，移入弹层保持打开且不改变表单布局', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 480 })
  await mockAdmin(page)
  await page.goto('/agents')
  const help = page.getByRole('button', { name: '权限模式说明', exact: true })
  await help.scrollIntoViewIfNeeded()
  const field = help.locator('..').locator('..')
  const height = (await field.boundingBox())!.height
  await help.hover()
  const content = page.locator('.ui-help-content')
  await expect(content).toBeVisible()
  expect((await field.boundingBox())!.height).toBe(height)
  const box = (await content.boundingBox())!
  expect(box.y).toBeGreaterThanOrEqual(0)
  expect(box.y + box.height).toBeLessThanOrEqual(480)
  const scrollArea = content.locator('.ui-help-body')
  expect(await scrollArea.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true)
  await content.hover()
  await page.mouse.wheel(0, 1000)
  await expect.poll(() => scrollArea.evaluate((el) => el.scrollTop)).toBeGreaterThan(0)
  await expect(content).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(content).toHaveCount(0)
})

test('表头问号与排序、列宽操作独立', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/statistics')
  const heading = page
    .getByRole('columnheader')
    .filter({ has: page.getByRole('button', { name: '缓存命中率', exact: true }) })
  const beforeSort = await heading.getAttribute('aria-sort')
  const beforeWidth = (await heading.boundingBox())!.width
  const beforeCalls = fixture.calls.length
  await heading.getByRole('button', { name: '缓存命中率说明', exact: true }).click()
  await expect(page.locator('.ui-help-content')).toBeVisible()
  expect(await heading.getAttribute('aria-sort')).toBe(beforeSort)
  expect((await heading.boundingBox())!.width).toBe(beforeWidth)
  expect(fixture.calls.length).toBe(beforeCalls)
  await page.keyboard.press('Escape')
  await heading.getByRole('button', { name: '缓存命中率', exact: true }).click()
  await expect(heading).toHaveAttribute('aria-sort', /ascending|descending/)
})

test.describe('触屏说明', () => {
  test.use({ hasTouch: true, viewport: { width: 390, height: 844 } })
  test('点按打开和关闭不会被延迟悬停重新打开', async ({ page }) => {
    await mockAdmin(page)
    await page.clock.install()
    await page.goto('/agents')
    const help = page.getByRole('button', { name: 'ANTHROPIC_MODEL说明', exact: true })
    await help.tap()
    await expect(page.locator('.ui-help-content')).toBeVisible()
    await page.clock.runFor(300)
    await help.tap()
    await page.clock.runFor(300)
    await expect(page.locator('.ui-help-content')).toHaveCount(0)
  })
})
