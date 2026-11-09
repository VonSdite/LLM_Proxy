import { expect, test } from '../browser-test'
import { mockProviderTable } from '../provider-fixtures'

test('鼠标打开没有隐藏模型字段的 Provider，搜索与复制可用，关闭后可再次打开', async ({
  page,
  context,
}) => {
  const fixture = await mockProviderTable(page)
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.goto('/')
  const models = page.getByRole('button', { name: '8 个模型', exact: true })
  await models.click()
  const dialog = page.getByRole('dialog', { name: 'gateway · 模型', exact: true })
  await expect(dialog.getByText('gateway/moonshotai/kimi-k2.5', { exact: true })).toBeVisible()
  await expect(dialog.getByRole('button', { name: '复制模型 ID', exact: true })).toHaveCount(8)
  await dialog.getByRole('textbox', { name: '搜索模型详情', exact: true }).fill('kimi')
  await expect(dialog.getByRole('button', { name: '复制模型 ID', exact: true })).toHaveCount(1)
  await dialog.getByRole('button', { name: '复制模型 ID', exact: true }).click()
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe('gateway/moonshotai/kimi-k2.5')
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await expect(models).toBeFocused()
  await models.click()
  await expect(dialog.getByRole('textbox', { name: '搜索模型详情', exact: true })).toHaveValue('')
  await expect(dialog.getByRole('button', { name: '复制模型 ID', exact: true })).toHaveCount(8)
  expect(fixture.unexpected).toEqual([])
})

test('模型以标签排列且数量入口在下方，隐藏模型可查看，删除确认使用醒目的红色', async ({ page }) => {
  const fixture = await mockProviderTable(page)
  await page.goto('/')
  await expect(page.locator('.provider-models').first().locator('.provider-model')).toHaveCount(6)
  const preview = page.locator('.provider-models').nth(2)
  const first = (await preview.locator('.provider-model').first().boundingBox())!
  const last = (await preview.locator('.provider-model').last().boundingBox())!
  const models = preview.getByRole('button', { name: '3 个模型', exact: true })
  const link = (await models.boundingBox())!
  expect(link.y).toBeGreaterThanOrEqual(last.y + last.height)
  expect(link.x).toBe(first.x)
  expect(
    await preview
      .locator('.provider-model')
      .last()
      .evaluate((el) => el.scrollWidth - el.clientWidth),
  ).toBeLessThanOrEqual(1)
  await models.click()
  const dialog = page.getByRole('dialog', { name: 'claude · 模型', exact: true })
  await expect(dialog.getByText('claude/claude-haiku-4-5', { exact: true })).toBeVisible()
  await expect(dialog.getByText('已隐藏', { exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '删除 gateway', exact: true }).click()
  await expect(page.locator('.ui-confirm p')).toHaveText('是否删除？')
  const confirm = page.locator('.ui-confirm').getByRole('button', { name: '确认删除', exact: true })
  await expect(confirm).toHaveCSS('background-color', 'rgb(217, 45, 58)')
  await expect(confirm).toHaveCSS('color', 'rgb(255, 255, 255)')
  await page.locator('.ui-confirm').getByRole('button', { name: '取消', exact: true }).click()
  await expect(page.locator('.ui-confirm')).toHaveCount(0)
  expect(fixture.calls.filter((call) => call.method !== 'GET')).toEqual([])
  expect(fixture.unexpected).toEqual([])
})

test('Provider 名称单行、长地址完整换行，单元格垂直居中，批量操作与认证对齐', async ({ page }) => {
  await page.setViewportSize({ width: 2560, height: 1000 })
  const fixture = await mockProviderTable(page)
  await page.goto('/')
  await page.getByRole('tab', { name: '全部', exact: true }).click()
  await expect(page.locator('.provider-address')).toHaveCount(5)
  for (const address of await page.locator('.provider-name .truncate').all()) {
    const measurement = await address.evaluate((el) => {
      const range = document.createRange()
      range.selectNodeContents(el)
      return {
        lines: new Set(Array.from(range.getClientRects(), (rect) => rect.top)).size,
        fontSize: parseFloat(getComputedStyle(el).fontSize),
      }
    })
    expect(measurement.lines).toBe(1)
    expect(measurement.fontSize).toBeGreaterThanOrEqual(14)
  }
  const auth = await page.locator('.provider-auth').evaluateAll((elements) =>
    elements.map((el) => ({
      x: el.getBoundingClientRect().x,
      fontSize: parseFloat(getComputedStyle(el).fontSize),
    })),
  )
  for (const item of auth) {
    expect(item.x).toBe(auth[0]!.x)
    expect(item.fontSize).toBeGreaterThanOrEqual(14)
  }
  const firstRow = page.locator('tbody tr').first()
  const rowBox = (await firstRow.boundingBox())!
  for (const control of await firstRow
    .locator(
      '.provider-name, .provider-endpoint, .provider-models, .provider-auth, [role="checkbox"], [role="switch"], .ui-table-order-actions, .table-actions',
    )
    .all()) {
    const box = (await control.boundingBox())!
    expect(
      Math.abs(box.y + box.height / 2 - rowBox.y - rowBox.height / 2),
      (await control.getAttribute('class')) || 'Table cell',
    ).toBeLessThanOrEqual(3)
  }
  await page.getByRole('checkbox', { name: '选择 gateway', exact: true }).click()
  const bar = page.getByRole('region', { name: '批量操作' })
  const count = (await bar.getByText('已选 1 项', { exact: true }).boundingBox())!
  const clear = (await bar.getByRole('button', { name: '清空选择', exact: true }).boundingBox())!
  const action = (await bar.getByRole('button', { name: '禁用', exact: true }).boundingBox())!
  expect(clear.x - count.x - count.width).toBeLessThanOrEqual(8)
  expect(action.x - clear.x - clear.width).toBeLessThanOrEqual(24)
  expect(action.y).toBeLessThan(count.y + count.height)
  const row = page.locator('tr[data-selected]')
  await page.mouse.move(0, 0)
  const background = await row.evaluate((el) => getComputedStyle(el).backgroundColor)
  await row.hover()
  expect(await row.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe(background)
  expect(fixture.unexpected).toEqual([])
})

test('API 地址下方仅显示启用功能与 Hook，长路径可聚焦查看且不撑宽单元格', async ({ page }) => {
  const fixture = await mockProviderTable(page)
  await page.goto('/')
  const endpoint = page.locator('.provider-endpoint')
  const metadata = endpoint.first().locator('.provider-metadata')
  await expect(metadata.locator('.ui-badge')).toHaveText([
    '证书校验',
    '强制上游流式',
    '安全脱敏',
    'Hook: routing/gateway.py',
  ])
  const url = (await endpoint.first().locator('.provider-address').boundingBox())!
  expect((await metadata.boundingBox())!.y).toBeGreaterThan(url.y + url.height)
  await expect(endpoint.nth(1).locator('.ui-badge')).toHaveText(['证书校验'])
  await expect(endpoint.nth(3).locator('.provider-metadata')).toHaveCount(0)
  const hook = endpoint.nth(2).locator('.provider-hook')
  await hook.focus()
  await expect(page.getByRole('tooltip')).toHaveText(fixture.providers[2]!.hook!)
  expect(await hook.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(1)
  const box = (await hook.boundingBox())!
  const cell = (await endpoint.nth(2).boundingBox())!
  expect(box.x + box.width).toBeLessThanOrEqual(cell.x + cell.width)
  expect(fixture.unexpected).toEqual([])
})

test('批量启停跟随所选状态，只提交需要变化的记录，失败保留选择并支持重试', async ({ page }) => {
  const fixture = await mockProviderTable(page)
  const writes: { names: string[]; action: string }[] = []
  let fail = true
  await page.route('**/api/providers/batch', async (route) => {
    const body = route.request().postDataJSON() as (typeof writes)[number]
    writes.push(body)
    if (fail) return route.fulfill({ status: 503, json: { error: '暂时无法保存' } })
    for (const provider of fixture.providers) {
      if (body.names.includes(provider.name)) provider.enabled = body.action === 'enable'
    }
    await route.fulfill({ json: { message: 'ok' } })
  })
  await page.goto('/')
  await page.getByRole('tab', { name: '全部', exact: true }).click()
  const enabled = page.getByRole('checkbox', { name: '选择 gateway', exact: true })
  const disabled = page.getByRole('checkbox', { name: '选择 backup', exact: true })
  const bar = page.getByRole('region', { name: '批量操作' })
  const enable = bar.getByRole('button', { name: '启用', exact: true })
  const disable = bar.getByRole('button', { name: '禁用', exact: true })
  await enabled.click()
  await expect(enable).toHaveCount(0)
  await expect(disable).toBeVisible()
  await disabled.click()
  await expect(enable).toBeVisible()
  await expect(disable).toBeVisible()
  await enabled.click()
  await expect(enable).toBeVisible()
  await expect(disable).toHaveCount(0)
  await enabled.click()
  await disable.click()
  await expect(page.getByRole('alert')).toContainText('暂时无法保存')
  await expect(enabled).toBeChecked()
  await expect(disabled).toBeChecked()
  await expect(bar).toContainText('已选 2 项')
  await expect(enable).toBeEnabled()
  await expect(disable).toBeEnabled()
  expect(writes).toEqual([{ names: ['gateway'], action: 'disable' }])
  fail = false
  await disable.click()
  await expect(bar).toHaveCount(0)
  await expect(page.getByRole('switch', { name: '启用 gateway', exact: true })).not.toBeChecked()
  expect(writes.at(-1)).toEqual({ names: ['gateway'], action: 'disable' })
  await enabled.click()
  await disabled.click()
  await expect(enable).toBeVisible()
  await expect(disable).toHaveCount(0)
  await enable.click()
  await expect(bar).toHaveCount(0)
  expect(writes.at(-1)).toEqual({ names: ['gateway', 'backup'], action: 'enable' })
  await expect(page.getByRole('switch', { name: '启用 gateway', exact: true })).toBeChecked()
  await expect(page.getByRole('switch', { name: '启用 backup', exact: true })).toBeChecked()
  await enabled.click()
  await disabled.click()
  await expect(enable).toHaveCount(0)
  await expect(disable).toBeVisible()
  await bar.getByRole('button', { name: '清空选择', exact: true }).click()
  await expect(bar).toHaveCount(0)
  expect(writes).toHaveLength(3)
  expect(fixture.unexpected).toEqual([])
})

test('长模型标签在窄列内省略，六个预览不会挤进相邻列', async ({ page }) => {
  await mockProviderTable(page)
  await page.goto('/')
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 })
    const address = page.locator('.provider-address').first()
    const addressSize = await address.evaluate((el) => ({
      overflow: el.scrollWidth - el.clientWidth,
      height: el.getBoundingClientRect().height,
      lineHeight: parseFloat(getComputedStyle(el).lineHeight),
    }))
    expect(addressSize.overflow).toBeLessThanOrEqual(1)
    expect(addressSize.height).toBeGreaterThan(addressSize.lineHeight)
    await expect(page.locator('.provider-models').first().locator('.provider-model')).toHaveCount(6)
    const bounds = await page.locator('.provider-model').evaluateAll((elements) =>
      elements.map((el) => {
        const tag = el.getBoundingClientRect()
        const cell = el.closest('td')!.getBoundingClientRect()
        return { left: tag.left - cell.left, right: cell.right - tag.right }
      }),
    )
    for (const tag of bounds) {
      expect(tag.left).toBeGreaterThanOrEqual(12)
      expect(tag.right).toBeGreaterThanOrEqual(11)
    }
    const longModel = page.locator('.provider-model').filter({ hasText: 'org/very-long-model' })
    const text = longModel.locator('span')
    await expect(text).toHaveCSS('text-overflow', 'ellipsis')
    expect(await text.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true)
    await longModel.scrollIntoViewIfNeeded()
    await longModel.hover()
    await expect(page.getByRole('tooltip')).toContainText('org/very-long-model')
  }
})

test('Provider 列宽偏好保留，地址达到上限后模型继续扩展，1440px 操作完整可见', async ({ page }) => {
  await mockProviderTable(page)
  await page.addInitScript(() => {
    localStorage.setItem('table-widths:providers', JSON.stringify({ api: 240, models: 216 }))
  })
  await page.goto('/')
  const resizer = page.getByRole('separator', { name: '调整API 地址列宽', exact: true })
  await expect(resizer).toHaveAttribute('aria-valuenow', '240')
  const address = page.getByRole('columnheader', { name: /^API 地址/ })
  const models = page.getByRole('columnheader', { name: /^模型/ })
  await page.setViewportSize({ width: 2560, height: 1000 })
  expect((await address.boundingBox())!.width).toBeCloseTo(720, 0)
  const initialModels = (await models.boundingBox())!.width
  await page.setViewportSize({ width: 3840, height: 1000 })
  expect((await address.boundingBox())!.width).toBeCloseTo(720, 0)
  expect((await models.boundingBox())!.width - initialModels).toBeCloseTo(1280, 0)
  await resizer.focus()
  await page.keyboard.press('ArrowRight')
  await expect(resizer).toHaveAttribute('aria-valuenow', '256')
  expect(
    await page.evaluate(() => JSON.parse(localStorage.getItem('table-widths:providers')!)),
  ).toMatchObject({ api: 256, models: 216 })
  await resizer.dblclick()
  await page.getByRole('separator', { name: '调整模型列宽', exact: true }).dblclick()
  await page.setViewportSize({ width: 1440, height: 1000 })
  await expect(page.getByRole('button', { name: '删除 gateway', exact: true })).toBeInViewport()
  expect(
    await page.locator('.ui-table-scroll').evaluate((el) => el.scrollWidth - el.clientWidth),
  ).toBeLessThanOrEqual(1)
})

test('Provider 名称编辑、模型和认证入口支持键盘与焦点返回，导出提示跟随筛选数量', async ({
  page,
}) => {
  const fixture = await mockProviderTable(page)
  await page.goto('/')
  const name = page.getByRole('button', { name: '编辑 Provider gateway', exact: true })
  const edit = page.getByRole('dialog', { name: '编辑 Provider', exact: true })
  await name.click()
  await expect(edit.getByRole('textbox', { name: 'Provider 名称', exact: true })).toHaveValue(
    'gateway',
  )
  await expect(edit.getByRole('textbox', { name: 'API 地址', exact: true })).toHaveValue(
    fixture.providers[0]!.api,
  )
  await page.keyboard.press('Escape')
  await expect(edit).toHaveCount(0)
  await expect(name).toBeFocused()
  await name.press('Enter')
  await expect(edit).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(name).toBeFocused()
  await expect(page.getByRole('checkbox', { name: '选择 gateway', exact: true })).not.toBeChecked()
  const address = page.locator('.provider-address').first()
  await address.focus()
  await expect(page.getByRole('tooltip')).toHaveText(fixture.providers[0]!.api)
  expect(await address.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(1)
  await page.keyboard.press('Escape')
  const auth = page.getByRole('button', { name: 'team-pool', exact: true }).first()
  await auth.focus()
  await page.keyboard.press('Enter')
  await expect(
    page.getByRole('dialog', { name: 'team-pool · 运行状态', exact: true }),
  ).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(auth).toBeFocused()
  const models = page.getByRole('button', { name: '8 个模型', exact: true })
  await models.focus()
  await page.keyboard.press('Enter')
  const dialog = page.getByRole('dialog', { name: 'gateway · 模型', exact: true })
  await expect(dialog.getByText('gateway/moonshotai/kimi-k2.5', { exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(models).toBeFocused()
  const exporting = page.getByRole('button', { name: '导出', exact: true })
  await exporting.hover()
  await expect(page.getByRole('tooltip')).toContainText('4 个 Provider')
  await page.getByRole('tab', { name: '已禁用', exact: true }).click()
  await exporting.hover()
  await expect(page.getByRole('tooltip')).toContainText('1 个 Provider')
  await page.getByRole('tab', { name: /Auth Groups/ }).click()
  const groupName = page.getByRole('button', { name: '编辑 Auth Group team-pool', exact: true })
  await groupName.click()
  const groupDialog = page.getByRole('dialog', { name: '编辑 Auth Group', exact: true })
  await expect(groupDialog.getByRole('textbox', { name: '名称', exact: true })).toHaveValue(
    'team-pool',
  )
  await page.keyboard.press('Escape')
  await expect(groupName).toBeFocused()
  await groupName.press('Enter')
  await expect(groupDialog).toBeVisible()
  await groupDialog.getByRole('button', { name: '取消', exact: true }).click()
  await expect(groupName).toBeFocused()
  expect(fixture.calls.filter((call) => call.method !== 'GET')).toEqual([])
  expect(fixture.unexpected).toEqual([])
})
