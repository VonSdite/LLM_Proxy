import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'
import { mockOAuthCards } from '../oauth-fixtures'

const pages = [
  { path: '/', search: '搜索配置', primary: '新增 Provider' },
  { path: '/oauth', search: '搜索认证文件', primary: '添加账号' },
  { path: '/model-mappings', search: '搜索模型映射', primary: '创建映射' },
  { path: '/api-keys', search: '搜索 API Key', primary: '创建 API Key' },
  { path: '/users', search: '搜索用户', primary: '添加用户' },
]

for (const width of [1920, 1440, 1024, 390, 320]) {
  test(`${width}px 列表操作靠左且直接可见，清空搜索保留焦点`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 })
    const fixture = await mockAdmin(page)
    for (const item of pages) {
      await page.goto(item.path)
      const heading = page.getByRole('heading', { level: 1 })
      const search = page.getByRole('textbox', { name: item.search, exact: true })
      const primary = page.getByRole('button', { name: item.primary, exact: true })
      await expect(search).toBeVisible()
      await expect(primary).toBeVisible()
      await expect(page.getByRole('region', { name: '批量操作' })).toHaveCount(0)
      const header = (await page.locator('.ui-page-header').boundingBox())!
      const action = (await primary.boundingBox())!
      expect(action.x).toBe((await heading.boundingBox())!.x)
      expect(action.y).toBeGreaterThanOrEqual(header.y + header.height)
      await expect(
        page
          .locator('.ui-list-toolbar-actions')
          .getByRole('button', { name: item.primary, exact: true }),
      ).toBeVisible()
      if (width <= 390) expect((await search.boundingBox())!.x).toBe(action.x)
      else expect((await search.boundingBox())!.x).toBeGreaterThan(action.x + action.width)
      if (item.path === '/') {
        const status = page
          .locator('.ui-list-toolbar-actions')
          .getByRole('tablist', { name: 'Provider 状态', exact: true })
        await expect(status).toBeVisible()
        if (width >= 1024) {
          const filter = (await status.boundingBox())!
          const importing = (await page
            .getByRole('button', { name: '导入', exact: true })
            .boundingBox())!
          expect(filter.x + filter.width).toBeLessThanOrEqual(importing.x)
          expect(filter.y + filter.height / 2).toBeCloseTo(importing.y + importing.height / 2, 0)
        }
      }
      await search.fill('lookup')
      await page.getByRole('button', { name: '清空搜索', exact: true }).click()
      await expect(search).toHaveValue('')
      await expect(search).toBeFocused()
      if (item.path !== '/api-keys') {
        for (const name of ['导入', '导出']) {
          const button = page.getByRole('button', { name, exact: true })
          await expect(button).toBeVisible()
          const box = (await button.boundingBox())!
          expect(box.x).toBeGreaterThanOrEqual(action.x)
          expect(box.x + box.width).toBeLessThanOrEqual(width)
        }
      }
      await expect(page.getByRole('button', { name: '更多', exact: true })).toHaveCount(0)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      )
    }
    expect(fixture.calls.filter((call) => call.method !== 'GET')).toEqual([])
    expect(fixture.unexpected).toEqual([])
  })
}

test('Provider 批量工具按需出现，清空选择恢复勾选框焦点并保留搜索节点', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/')
  const search = await page.getByRole('textbox', { name: '搜索配置' }).elementHandle()
  const checkbox = page.getByRole('checkbox', { name: '选择 main', exact: true })
  await checkbox.click()
  const bar = page.getByRole('region', { name: '批量操作' })
  await expect(bar).toContainText('已选 1 项')
  const clear = bar.getByRole('button', { name: '清空选择', exact: true })
  await expect(clear).toHaveText('')
  await clear.hover()
  await expect(page.locator('.ui-tooltip')).toContainText('清空选择')
  await clear.press('Enter')
  await expect(bar).toHaveCount(0)
  await expect(checkbox).not.toBeChecked()
  await expect(checkbox).toBeFocused()
  expect(await search!.evaluate((element) => element.isConnected)).toBe(true)
  expect(fixture.calls.filter((call) => call.method !== 'GET')).toEqual([])
})

test('窄屏 OAuth 批量选择保留筛选入口，清空选择不触发写入', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 1000 })
  const fixture = await mockOAuthCards(page)
  await page.goto('/oauth')
  await page.getByRole('checkbox', { name: '选择当前筛选的账号', exact: true }).click()
  const bar = page.getByRole('region', { name: '批量操作' })
  await expect(bar).toContainText('已选 2 项')
  await page.getByRole('tab', { name: '全部 6', exact: true }).click()
  await expect(bar).toContainText('已选 2 项')
  await bar.getByRole('button', { name: '清空选择' }).click()
  await expect(page.getByRole('checkbox', { checked: true })).toHaveCount(0)
  await expect(bar).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(fixture.calls.filter((call) => call.method !== 'GET')).toEqual([])
})

test('OAuth 批量启停与 Provider 使用相同的状态规则，只请求需要变化的账号', async ({ page }) => {
  await page.clock.install({ time: new Date('2026-11-07T02:00:00Z') })
  const fixture = await mockOAuthCards(page)
  const writes: string[] = []
  for (const action of ['enable', 'disable']) {
    await page.route(`**/api/oauth/codex/auth-files/*/${action}`, async (route) => {
      const name = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-2)!)
      const file = fixture.files.find((file) => file.name === name)!
      file.enabled = action === 'enable'
      file.disabled = action === 'disable'
      writes.push(`${action}:${name}`)
      await route.fulfill({ json: { message: 'ok' } })
    })
  }
  await page.goto('/oauth')
  await page.getByRole('tab', { name: '全部 6', exact: true }).click()
  const bar = page.getByRole('region', { name: '批量操作' })
  const enable = bar.getByRole('button', { name: '启用', exact: true })
  const disable = bar.getByRole('button', { name: '禁用', exact: true })
  const active = page.getByRole('checkbox', { name: `选择 ${fixture.files[0]!.name}`, exact: true })
  const inactive = page.getByRole('checkbox', {
    name: `选择 ${fixture.files[2]!.name}`,
    exact: true,
  })
  await active.click()
  await expect(enable).toHaveCount(0)
  await expect(disable).toBeVisible()
  await inactive.click()
  await expect(enable).toBeVisible()
  await expect(disable).toBeVisible()
  await active.click()
  await expect(enable).toBeVisible()
  await expect(disable).toHaveCount(0)
  await active.click()
  await enable.click()
  await expect(disable).toBeEnabled()
  await expect(enable).toHaveCount(0)
  expect(writes).toEqual([`enable:${fixture.files[2]!.name}`])
  await disable.click()
  await expect(enable).toBeEnabled()
  await expect(disable).toHaveCount(0)
  expect(writes.slice(1)).toEqual(
    fixture.files
      .filter(
        (file) => file.name === fixture.files[0]!.name || file.name === fixture.files[2]!.name,
      )
      .map((file) => `disable:${file.name}`),
  )
  expect(fixture.unexpected).toEqual([])
})

test('Provider 导出跟随状态与搜索，所选独立，空结果不能导出且下载不刷新列表', async ({ page }) => {
  const fixture = await mockAdmin(page)
  let reads = 0
  const exported: unknown[] = []
  await page.route('**/api/providers', async (route) => {
    reads++
    await route.fulfill({
      json: [
        { ...fixture.provider, name: 'alpha-enabled' },
        { ...fixture.provider, name: 'beta-enabled' },
        { ...fixture.provider, name: 'alpha-disabled', enabled: false },
      ],
    })
  })
  await page.route('**/api/providers/export', async (route) => {
    exported.push(route.request().postDataJSON())
    await route.fulfill({ body: '{}', contentType: 'application/json' })
  })
  await page.goto('/')
  const button = page.getByRole('button', { name: '导出', exact: true })
  const search = page.getByRole('textbox', { name: '搜索配置' })
  for (const [state, keyword, names] of [
    ['已启用', '', ['alpha-enabled', 'beta-enabled']],
    ['已禁用', '', ['alpha-disabled']],
    ['全部', '', ['alpha-enabled', 'beta-enabled', 'alpha-disabled']],
    ['全部', 'alpha', ['alpha-enabled', 'alpha-disabled']],
    ['已启用', 'alpha', ['alpha-enabled']],
  ] as const) {
    await page.getByRole('tab', { name: state, exact: true }).click()
    await search.fill(keyword)
    const download = page.waitForEvent('download')
    await button.click()
    await download
    await expect(button).toBeEnabled()
    expect(exported.at(-1)).toEqual({ names })
  }
  await search.clear()
  await page.getByRole('checkbox', { name: '选择 beta-enabled', exact: true }).click()
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出所选', exact: true }).click()
  await download
  expect(exported.at(-1)).toEqual({ names: ['beta-enabled'] })
  await search.fill('no-match')
  await expect(button).toBeDisabled()
  expect(exported).toHaveLength(6)
  expect(reads).toBe(1)
  expect(fixture.unexpected).toEqual([])
})

test('模型映射导出仅包含当前筛选和搜索，空结果不回退到全量', async ({ page }) => {
  const fixture = await mockAdmin(page)
  const exported: unknown[] = []
  await page.route('**/api/model-mappings', (route) =>
    route.fulfill({
      json: {
        enabled: true,
        available_targets: [],
        model_mappings: [
          { id: 'alpha', enabled: true, strategy: 'highest_priority', targets: [] },
          { id: 'beta', enabled: false, strategy: 'highest_priority', targets: [] },
        ],
      },
    }),
  )
  await page.route('**/api/model-mappings/export', async (route) => {
    exported.push(route.request().postDataJSON())
    await route.fulfill({ body: '{}', contentType: 'application/json' })
  })
  await page.goto('/model-mappings')
  const button = page.getByRole('button', { name: '导出', exact: true })
  for (const [state, ids] of [
    ['已启用', ['alpha']],
    ['已禁用', ['beta']],
    ['全部', ['alpha', 'beta']],
  ] as const) {
    await page.getByRole('tab', { name: state, exact: true }).click()
    const download = page.waitForEvent('download')
    await button.click()
    await download
    await expect(button).toBeEnabled()
    expect(exported.at(-1)).toEqual({ mapping_ids: ids })
  }
  const search = page.getByRole('textbox', { name: '搜索模型映射' })
  await search.fill('beta')
  const download = page.waitForEvent('download')
  await button.click()
  await download
  expect(exported.at(-1)).toEqual({ mapping_ids: ['beta'] })
  await search.fill('no-match')
  await expect(button).toBeDisabled()
  expect(exported).toHaveLength(4)
  expect(fixture.unexpected).toEqual([])
})

test('用户导出等待搜索结果，包含全部匹配分页且下载不刷新列表', async ({ page }) => {
  const fixture = await mockAdmin(page)
  let releaseSearch: () => void = () => undefined
  const searchReady = new Promise<void>((resolve) => {
    releaseSearch = resolve
  })
  const searches: string[] = []
  let exported: unknown
  await page.route('**/api/users?*', async (route) => {
    const url = new URL(route.request().url())
    const keyword = url.searchParams.get('keyword') || ''
    searches.push(keyword)
    if (keyword === 'alpha') await searchReady
    await route.fulfill({
      json: {
        users:
          keyword === 'no-match'
            ? []
            : [
                {
                  id: 1,
                  username: 'alpha',
                  ip_address: '10.0.0.1',
                  whitelist_access_enabled: true,
                },
              ],
        total: keyword === 'no-match' ? 0 : 51,
        available_models: [],
      },
    })
  })
  await page.route('**/api/users/export', async (route) => {
    exported = route.request().postDataJSON()
    await route.fulfill({ body: '{}', contentType: 'application/json' })
  })
  await page.goto('/users')
  const button = page.getByRole('button', { name: '导出', exact: true })
  await expect(button).toBeEnabled()
  const search = page.getByRole('textbox', { name: '搜索用户', exact: true })
  await search.fill('alpha')
  await expect(button).toBeDisabled()
  await expect.poll(() => searches.at(-1)).toBe('alpha')
  await expect(button).toBeDisabled()
  releaseSearch()
  await expect(button).toBeEnabled()
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(button).toBeEnabled()
  const reads = searches.length
  const download = page.waitForEvent('download')
  await button.click()
  await download
  await expect(button).toBeEnabled()
  expect(exported).toEqual({ keyword: 'alpha' })
  expect(searches).toHaveLength(reads)
  await search.fill('no-match')
  await expect(page.locator('.ui-pagination')).toContainText('共 0 项')
  await expect(button).toBeDisabled()
  expect(fixture.unexpected).toEqual([])
})

test('认证文件直接导出当前状态和搜索结果，空结果禁用导出', async ({ page }) => {
  const fixture = await mockOAuthCards(page)
  let exported: unknown
  await page.route('**/api/oauth/codex/auth-files/export', async (route) => {
    exported = route.request().postDataJSON()
    await route.fulfill({ body: 'fixture-archive', contentType: 'application/zip' })
  })
  await page.goto('/oauth')
  const button = page.getByRole('button', { name: '导出', exact: true })
  for (const [state, names] of [
    ['可用 2', fixture.files.slice(0, 2).map((file) => file.name)],
    ['已禁用 1', [fixture.files[2]!.name]],
    ['全部 6', fixture.files.map((file) => file.name)],
  ] as const) {
    await page.getByRole('tab', { name: state, exact: true }).click()
    const download = page.waitForEvent('download')
    await button.click()
    await download
    await expect(button).toBeEnabled()
    expect(exported).toEqual({ names })
  }
  const search = page.getByRole('textbox', { name: '搜索认证文件', exact: true })
  await search.fill('studio')
  const download = page.waitForEvent('download')
  await button.click()
  await download
  expect(exported).toEqual({ names: [fixture.files[0]!.name] })
  await search.fill('no-match')
  await expect(button).toBeDisabled()
  expect(fixture.unexpected).toEqual([])
})

test('直接导入支持键盘与焦点返回，认证文件支持多选且模型目录可直接打开', async ({ page }) => {
  const fixture = await mockAdmin(page)
  for (const path of ['/', '/users', '/model-mappings', '/oauth']) {
    await page.goto(path)
    const importing = page.getByRole('button', { name: '导入', exact: true })
    if (path === '/oauth') {
      const chooser = page.waitForEvent('filechooser')
      await importing.click()
      expect((await chooser).isMultiple()).toBe(true)
    }
    const trigger =
      path === '/oauth' ? page.getByRole('button', { name: '模型目录', exact: true }) : importing
    await trigger.focus()
    await trigger.press('Enter')
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expect
      .poll(() => dialog.evaluate((element) => element.contains(document.activeElement)))
      .toBe(true)
    await page.keyboard.press('Escape')
    await expect(dialog).toHaveCount(0)
    await expect(trigger).toBeFocused()
  }
  expect(fixture.unexpected).toEqual([])
})
