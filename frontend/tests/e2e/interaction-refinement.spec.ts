import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'
import type { ModelMapping } from '../../src/api/model-mappings'

function gate() {
  let release!: () => void
  const promise = new Promise<void>((resolve) => {
    release = resolve
  })
  return { promise, release }
}

test('Provider 开关局部等待、失败恢复及重试均保持列宽、节点与选择', async ({ page }) => {
  const fixture = await mockAdmin(page)
  const rows = [fixture.provider, { ...fixture.provider, name: 'backup' }]
  let reads = 0
  await page.route('**/api/providers', (route) => {
    reads++
    return route.fulfill({ json: rows })
  })
  let pending = gate()
  let fail = true
  let writes = 0
  await page.route('**/api/providers/*/disable', async (route) => {
    writes++
    const main = route.request().url().includes('/main/')
    if (main) await pending.promise
    if (main && fail) return route.fulfill({ status: 503, json: { error: '暂时无法保存' } })
    const row = rows[main ? 0 : 1]!
    row.enabled = false
    return route.fulfill({ json: row })
  })
  await page.goto('/')
  await page.getByRole('tab', { name: '全部', exact: true }).click()
  await page.getByRole('checkbox', { name: '选择 main', exact: true }).click()
  const selection = page.getByRole('region', { name: '批量操作' })
  const enableSelected = selection.getByRole('button', { name: '启用', exact: true })
  const disableSelected = selection.getByRole('button', { name: '禁用', exact: true })
  const row = page.locator('tbody tr').first()
  const original = await row.elementHandle()
  const headers = () =>
    page.locator('th').evaluateAll((items) =>
      items.map((item) => {
        const { x, width } = item.getBoundingClientRect()
        return { x, width }
      }),
    )
  const toggle = page.getByRole('switch', { name: '启用 main', exact: true })
  await toggle.scrollIntoViewIfNeeded()
  const before = await headers()
  const scrollBefore = await page.locator('.ui-table-scroll').evaluate((el) => el.scrollLeft)
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-busy', 'true')
  await expect(enableSelected).toHaveCount(0)
  await expect(disableSelected).toBeDisabled()
  await expect(toggle).not.toBeChecked()
  await expect(toggle).toBeFocused()
  expect(await headers()).toEqual(before)
  expect(await row.evaluate((el, original) => el === original, original)).toBe(true)
  expect(await page.locator('.ui-table-scroll').evaluate((el) => el.scrollLeft)).toBe(scrollBefore)
  await expect(page.getByRole('checkbox', { name: '选择 main', exact: true })).toBeChecked()
  await expect(page.getByRole('switch', { name: '启用 backup', exact: true })).toBeEnabled()
  // 忙碌控件仍保留键盘焦点，空格不重复提交。
  await page.keyboard.press('Space')
  expect(writes).toBe(1)
  await page.getByRole('switch', { name: '启用 backup', exact: true }).click()
  await expect.poll(() => writes).toBe(2)
  pending.release()
  await expect(toggle).toBeChecked()
  await expect(page.getByText('暂时无法保存', { exact: true })).toBeVisible()
  await expect(enableSelected).toHaveCount(0)
  await expect(disableSelected).toBeEnabled()
  expect(reads).toBe(1)
  pending = gate()
  fail = false
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-busy', 'true')
  pending.release()
  await expect(toggle).not.toHaveAttribute('aria-busy', 'true')
  await expect(toggle).not.toBeChecked()
  await expect(enableSelected).toBeEnabled()
  await expect(disableSelected).toHaveCount(0)
  expect(await headers()).toEqual(before)
  expect(reads).toBe(1)
  expect(fixture.unexpected).toEqual([])
})

test('已启用筛选下等待响应期间保留当前行，成功后才移出列表', async ({ page }) => {
  const fixture = await mockAdmin(page)
  const pending = gate()
  await page.route('**/api/providers/main/disable', async (route) => {
    await pending.promise
    fixture.provider.enabled = false
    await route.fulfill({ json: fixture.provider })
  })
  await page.goto('/')
  await page.getByRole('switch', { name: '启用 main', exact: true }).click()
  await expect(page.locator('tbody tr')).toHaveCount(1)
  pending.release()
  await expect(page.locator('tbody tr')).toHaveCount(0)
  await page.getByRole('tab', { name: '已禁用', exact: true }).click()
  await expect(page.getByRole('switch', { name: '启用 main', exact: true })).not.toBeChecked()
  expect(fixture.calls.filter((call) => call.path === '/api/providers')).toHaveLength(1)
})

test('映射的父开关与目标互斥，目标状态更新保留整行和顺序列', async ({ page }) => {
  await mockAdmin(page)
  const response = page.waitForResponse('**/api/model-mappings')
  await page.goto('/model-mappings')
  const mapping = ((await (await response).json()) as { model_mappings: ModelMapping[] })
    .model_mappings[0]!
  const pending = gate()
  await page.route('**/api/model-mappings/smart/targets/toggle', async (route) => {
    await pending.promise
    mapping.targets[0]!.enabled = false
    mapping.targets[0]!.status = 'disabled'
    await route.fulfill({ json: mapping })
  })
  const row = page.locator('tbody tr').first()
  const original = await row.elementHandle()
  const target = page.getByRole('switch', {
    name: `${mapping.targets[0]!.model_id} 启用状态`,
    exact: true,
  })
  await target.click()
  await expect(target).toHaveAttribute('aria-busy', 'true')
  await expect(page.getByRole('switch', { name: '启用映射 smart', exact: true })).toBeDisabled()
  await expect(page.getByRole('columnheader', { name: '顺序', exact: true })).toBeVisible()
  pending.release()
  await expect(target).not.toHaveAttribute('aria-busy', 'true')
  await expect(target).not.toBeChecked()
  await expect(page.getByRole('switch', { name: '启用映射 smart', exact: true })).toBeEnabled()
  expect(await row.evaluate((el, original) => el === original, original)).toBe(true)
})

for (const item of [
  {
    path: '/users',
    endpoint: '/api/users/1',
    label: '开发用户 白名单访问',
    field: 'whitelist_access_enabled',
  },
  { path: '/api-keys', endpoint: '/api/api-keys/1', label: '启用 开发 Key', field: 'enabled' },
]) {
  test(`${item.path} 开关使用显式目标状态并保留当前分页和选择`, async ({ page }) => {
    const fixture = await mockAdmin(page)
    const pending = gate()
    let body: unknown
    await page.route(`**${item.endpoint}`, async (route) => {
      expect(route.request().method()).toBe('PUT')
      body = route.request().postDataJSON()
      await pending.promise
      await route.fulfill({ json: { message: 'ok' } })
    })
    await page.goto(item.path)
    if (item.path === '/users')
      await page.getByRole('checkbox', { name: '选择 1', exact: true }).click()
    const toggle = page.getByRole('switch', { name: item.label, exact: true })
    const original = await page.locator('tbody tr').elementHandle()
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-busy', 'true')
    pending.release()
    await expect(toggle).not.toHaveAttribute('aria-busy', 'true')
    await expect(toggle).not.toBeChecked()
    expect(body).toEqual({ [item.field]: false })
    expect(fixture.calls.filter((call) => call.path === `/api${item.path}`)).toHaveLength(1)
    expect(
      await page.locator('tbody tr').evaluate((el, original) => el === original, original),
    ).toBe(true)
    if (item.path === '/users')
      await expect(page.getByRole('checkbox', { name: '选择 1', exact: true })).toBeChecked()
    expect(fixture.unexpected).toEqual([])
  })
}

test('设置分组保留草稿，局部保存与重新读取具有明确边界', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/settings')
  await expect(page.getByRole('tab')).toHaveCount(0)
  for (const title of [
    '基础设置',
    '客户端 IP',
    'API Key',
    '模型映射',
    'OAuth 网络',
    '日志与调试',
  ]) {
    await expect(page.getByRole('form', { name: title, exact: true })).toBeVisible()
  }
  const basic = page.getByRole('form', { name: '基础设置', exact: true })
  await expect(basic.getByRole('button', { name: '保存', exact: true })).toBeDisabled()
  await basic.getByRole('textbox', { name: '监听地址', exact: true }).fill('0.0.0.0')
  const debug = page.getByRole('form', { name: '日志与调试', exact: true })
  await debug.getByRole('textbox', { name: '日志目录', exact: true }).fill('logs/custom')
  await debug.getByRole('button', { name: '保存', exact: true }).click()
  await expect(debug.getByRole('button', { name: '保存', exact: true })).toBeDisabled()
  await expect(basic.getByRole('textbox', { name: '监听地址', exact: true })).toHaveValue('0.0.0.0')
  await expect(basic.getByRole('button', { name: '保存', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '重新读取', exact: true }).click()
  const confirm = page.getByRole('dialog', { name: '重新读取配置？', exact: true })
  await expect(confirm).toBeVisible()
  await confirm.getByRole('button', { name: '继续编辑', exact: true }).click()
  await expect(basic.getByRole('textbox', { name: '监听地址', exact: true })).toHaveValue('0.0.0.0')
  await page.getByRole('button', { name: '重新读取', exact: true }).click()
  await confirm.getByRole('button', { name: '丢弃并重新读取', exact: true }).click()
  await expect(confirm).not.toBeVisible()
  await expect(basic.getByRole('textbox', { name: '监听地址', exact: true })).toHaveValue(
    '127.0.0.1',
  )
  await expect(debug.getByRole('textbox', { name: '日志目录', exact: true })).toHaveValue(
    'logs/custom',
  )
  expect(fixture.calls.filter((call) => call.path.endsWith('/basic'))).toHaveLength(0)
  expect(fixture.unexpected).toEqual([])
})

test('设置保存时锁定当前分组，失败反馈留在分组内且保留其他草稿', async ({ page }) => {
  const fixture = await mockAdmin(page)
  const pending = gate()
  await page.route('**/api/settings/system/basic', async (route) => {
    await pending.promise
    await route.fulfill({ status: 503, json: { error: '配置暂时不可写' } })
  })
  await page.goto('/settings')
  const basic = page.getByRole('form', { name: '基础设置', exact: true })
  await basic.getByRole('textbox', { name: '监听地址', exact: true }).fill('0.0.0.0')
  await basic.getByRole('button', { name: '保存', exact: true }).click()
  await expect(basic.getByRole('textbox', { name: '监听地址', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '重新读取', exact: true })).toBeDisabled()
  await page.getByRole('textbox', { name: '日志目录', exact: true }).fill('logs/other')
  pending.release()
  await expect(basic.getByRole('alert')).toContainText('配置暂时不可写')
  await expect(basic.getByRole('textbox', { name: '监听地址', exact: true })).toHaveValue('0.0.0.0')
  await expect(basic.getByRole('button', { name: '保存', exact: true })).toBeEnabled()
  await expect(page.getByRole('textbox', { name: '日志目录', exact: true })).toHaveValue(
    'logs/other',
  )
  expect(fixture.unexpected).toEqual([])
})

for (const path of ['/', '/settings']) {
  test(`${path} 在 2560px 使用可用宽度并保持紧凑的操作列`, async ({ page }) => {
    await page.setViewportSize({ width: 2560, height: 1000 })
    await mockAdmin(page)
    await page.goto(path)
    const surface = page.locator(path === '/' ? '.ui-table-wrap' : '.settings-panel')
    await expect(surface).toBeVisible()
    const box = (await surface.boundingBox())!
    expect(box.x).toBe(240)
    expect(2560 - box.x - box.width).toBe(24)
    if (path === '/') {
      await expect(page.getByRole('columnheader', { name: '顺序', exact: true })).toHaveText('')
      await expect(page.getByRole('columnheader', { name: /^上游协议/ })).toBeVisible()
      expect(
        (await page.getByRole('columnheader', { name: '顺序', exact: true }).boundingBox())!.width,
      ).toBeCloseTo(40, 0)
      expect(
        (await page.getByRole('columnheader', { name: /^操作/ }).boundingBox())!.width,
      ).toBeCloseTo(168, 0)
      const address = (await page.getByRole('columnheader', { name: /^API 地址/ }).boundingBox())!
      const provider = (await page.getByRole('columnheader', { name: /^Provider/ }).boundingBox())!
      expect(provider.width).toBeCloseTo(176, 0)
      expect(address.width).toBeGreaterThan(300)
      expect(address.width).toBeLessThanOrEqual(720)
    }
  })
}

test('OAuth 开关只更新当前卡片并保持选择与焦点', async ({ page }) => {
  const fixture = await mockAdmin(page)
  const pending = gate()
  await page.route('**/api/oauth/codex/auth-files/account-demo.json/disable', async (route) => {
    await pending.promise
    await route.fulfill({ json: { message: 'ok' } })
  })
  await page.goto('/oauth')
  await page.getByRole('tab', { name: /^全部 / }).click()
  const selected = page.getByRole('checkbox', { name: '选择 account-demo.json', exact: true })
  await selected.click()
  const card = page.locator('.oauth-file')
  const original = await card.elementHandle()
  const toggle = page.getByRole('switch', { name: '启用 account-demo.json', exact: true })
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-busy', 'true')
  await expect(toggle).toBeFocused()
  pending.release()
  await expect(toggle).not.toHaveAttribute('aria-busy', 'true')
  await expect(toggle).not.toBeChecked()
  await expect(selected).toBeChecked()
  expect(await card.evaluate((el, original) => el === original, original)).toBe(true)
  expect(fixture.calls.filter((call) => call.path === '/api/oauth/codex/auth-files')).toHaveLength(
    1,
  )
  expect(fixture.unexpected).toEqual([])
})

test('1440px 下 Provider 启停与操作直接可见，无需横向滚动', async ({ page }) => {
  await mockAdmin(page)
  await page.goto('/')
  await expect(page.getByRole('button', { name: '删除 main', exact: true })).toBeInViewport()
  const overflow = await page
    .locator('.ui-table-scroll')
    .evaluate((el) => el.scrollWidth - el.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
})

test('认证条目开关保留表格，返回运行状态后同步计数', async ({ page }) => {
  const fixture = await mockAdmin(page)
  const runtime = {
    name: 'pool',
    summary: { entry_count: 1, available_count: 1, disabled_count: 0, cooldown_count: 0 },
    entries: [{ id: 'key-a', enabled: true, disabled: false, status: 'available', inflight: 0 }],
  }
  await page.route('**/api/auth-groups', (route) =>
    route.fulfill({
      json: [
        {
          name: 'pool',
          strategy: 'least_inflight',
          entries: [{ id: 'key-a' }],
          summary: runtime.summary,
        },
      ],
    }),
  )
  await page.route('**/api/auth-groups/pool/runtime', (route) => route.fulfill({ json: runtime }))
  const pending = gate()
  await page.route('**/api/auth-groups/pool/entries/key-a/disable', async (route) => {
    await pending.promise
    runtime.entries[0]!.disabled = true
    runtime.entries[0]!.status = 'disabled'
    runtime.summary.available_count = 0
    runtime.summary.disabled_count = 1
    await route.fulfill({ json: { message: 'ok' } })
  })
  await page.goto('/')
  await page.getByRole('tab', { name: /Auth Groups/ }).click()
  await page.getByRole('button', { name: '运行状态', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'pool · 运行状态', exact: true })
  const toggle = dialog.getByRole('switch', { name: '启用 key-a', exact: true })
  const original = await dialog.locator('tbody tr').elementHandle()
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-busy', 'true')
  pending.release()
  await expect(toggle).not.toHaveAttribute('aria-busy', 'true')
  await expect(toggle).not.toBeChecked()
  await expect(dialog.getByText('禁用 1', { exact: true })).toBeVisible()
  expect(
    await dialog.locator('tbody tr').evaluate((el, original) => el === original, original),
  ).toBe(true)
  expect(fixture.unexpected).toEqual([])
})
