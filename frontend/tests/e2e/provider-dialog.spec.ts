import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

test('模型测试按行锁定，其他行可编辑和测试，拖动后结果对应原模型', async ({ page }) => {
  const fixture = await mockAdmin(page)
  fixture.provider.model_list = ['model-a', 'model-b', 'model-c']
  const releases = new Map<string, () => void>()
  await page.route('**/api/providers/test-models', async (route) => {
    const model = route.request().postDataJSON().models[0] as string
    await new Promise<void>((resolve) => releases.set(model, resolve))
    await route.fulfill({
      json: {
        results: [
          {
            requested_model: model,
            available: true,
            first_token_latency_ms: model === 'model-a' ? 50 : 80,
            tps: model === 'model-a' ? 20 : 30,
            response_model: model,
            error: null,
          },
        ],
      },
    })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '编辑 Provider main', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '编辑 Provider', exact: true })
  const row = (id: string) => dialog.locator(`tr[data-sort-id="${id}"]`)
  await row('1').getByRole('button', { name: '测试', exact: true }).click()
  await expect(row('1').getByRole('textbox')).toBeDisabled()
  await expect(row('1').getByRole('button', { name: '移除模型', exact: true })).toBeDisabled()
  await expect(row('2').getByRole('textbox')).toBeEnabled()
  await row('3').getByRole('textbox').fill('edited-c')
  await row('2').getByRole('button', { name: '测试', exact: true }).click()
  await expect.poll(() => releases.size).toBe(2)
  const source = (await row('1')
    .getByRole('button', { name: '拖动 1 排序', exact: true })
    .boundingBox())!
  const last = (await row('3').boundingBox())!
  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2)
  await page.mouse.down()
  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2 + 10)
  await page.mouse.move(source.x + source.width / 2, last.y + last.height - 4, { steps: 12 })
  await page.mouse.up()
  await expect(dialog.locator('tbody tr[data-sort-id]').last()).toHaveAttribute('data-sort-id', '1')
  releases.get('model-b')!()
  await expect(row('2')).toContainText('80 ms')
  await expect(row('1')).toContainText('测试中')
  await expect(row('2').getByRole('textbox')).toBeEnabled()
  releases.get('model-a')!()
  await expect(row('1')).toContainText('50 ms')
  await expect(row('1')).toContainText('20 tok/s')
  await expect(row('2')).toContainText('30 tok/s')
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  expect(
    fixture.calls.find((call) => call.path === '/api/providers/main' && call.method === 'PUT')
      ?.body,
  ).toMatchObject({ model_list: ['model-b', 'edited-c', 'model-a'] })
  expect(fixture.unexpected).toEqual([])
})

test('模型结果分列，错误位于操作列且批量操作使用简短文字', async ({ page }) => {
  await mockAdmin(page)
  let attempt = 0
  const error = "The requested model does not exist. Please check the 'model' parameter."
  await page.route('**/api/providers/test-models', async (route) => {
    const success = ++attempt === 1
    await route.fulfill({
      json: {
        results: [
          {
            requested_model: 'model',
            available: success,
            first_token_latency_ms: success ? 1746.64 : null,
            tps: success ? 59.12 : null,
            response_model: 'model',
            error: success ? null : error,
          },
        ],
      },
    })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '编辑 Provider main', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '编辑 Provider', exact: true })
  for (const name of ['结果', '首字延迟', 'TPS']) {
    await expect(dialog.getByRole('columnheader', { name: new RegExp(`^${name}`) })).toBeVisible()
  }
  await dialog.getByRole('button', { name: '首字延迟说明', exact: true }).hover()
  await expect(page.locator('.ui-help-content')).toContainText('从发起上游请求')
  await expect(page.locator('.ui-help-content')).toContainText('思考片段也算首字')
  await page.keyboard.press('Escape')
  await dialog.getByRole('button', { name: 'TPS说明', exact: true }).hover()
  await expect(page.locator('.ui-help-content')).toContainText('上游输出 Token 数 − 1')
  await expect(page.locator('.ui-help-content')).toContainText('排除首字等待')
  await expect(page.locator('.ui-help-content')).toContainText('隐藏思考')
  await page.keyboard.press('Escape')
  const rows = dialog.locator('tbody > tr[data-sort-id]')
  await rows.nth(0).getByRole('button', { name: '测试', exact: true }).click()
  await expect(rows.nth(0).getByRole('cell', { name: '1746.64 ms', exact: true })).toBeVisible()
  await expect(rows.nth(0).getByRole('cell', { name: '59.12 tok/s', exact: true })).toBeVisible()
  await rows.nth(1).getByRole('button', { name: '测试', exact: true }).click()
  await expect(rows.nth(1).getByRole('cell', { name: '失败', exact: true })).toBeVisible()
  const actions = rows.nth(1).getByRole('cell').last()
  await expect(actions).toContainText(error)
  const errorText = actions.getByText(error, { exact: true })
  await expect(errorText).toBeVisible()
  const errorLayout = await errorText.evaluate((element) => ({
    width: element.clientWidth,
    fullWidth: element.scrollWidth,
    height: element.clientHeight,
    fullHeight: element.scrollHeight,
    lineHeight: Number.parseFloat(getComputedStyle(element).lineHeight),
  }))
  expect(errorLayout.fullWidth).toBeLessThanOrEqual(errorLayout.width)
  expect(errorLayout.fullHeight).toBeLessThanOrEqual(errorLayout.height)
  expect(errorLayout.height).toBeGreaterThan(errorLayout.lineHeight)
  await expect(dialog.getByRole('button', { name: '模型测试详情' })).toHaveCount(0)
  await dialog.getByRole('checkbox', { name: '选择 1', exact: true }).click()
  const selection = dialog.getByRole('region', { name: '批量操作' })
  await expect(selection.getByRole('button', { name: '测试', exact: true })).toBeVisible()
  await expect(selection.getByRole('button', { name: '删除', exact: true })).toBeVisible()
})

test('Provider 三列排列，自定义代理在原列内展开且切换保留草稿', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/')
  await page.getByRole('button', { name: '编辑 Provider main', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '编辑 Provider', exact: true })
  const proxy = dialog.getByRole('combobox', { name: '连接方式', exact: true })
  const timeout = dialog.getByRole('textbox', { name: /请求超时/ })
  const retries = dialog.getByRole('textbox', { name: '最大尝试次数', exact: true })
  const before = (await timeout.boundingBox())!
  const dialogBefore = (await dialog.boundingBox())!
  await proxy.click()
  await page.getByRole('option', { name: '自定义代理', exact: true }).click()
  const address = dialog.getByRole('textbox', { name: '代理地址', exact: true })
  await address.fill('socks5h://127.0.0.1:1080')
  const proxyBox = (await proxy.boundingBox())!
  const addressBox = (await address.boundingBox())!
  expect(proxyBox.width).toBeLessThanOrEqual(124)
  expect(addressBox.x).toBeGreaterThan(proxyBox.x + proxyBox.width)
  expect(addressBox.y).toBe(proxyBox.y)
  expect(addressBox.x - proxyBox.x - proxyBox.width).toBeLessThanOrEqual(12)
  const addressHelp = dialog.getByRole('button', { name: '代理地址说明', exact: true })
  const helpBox = (await addressHelp.boundingBox())!
  expect(helpBox.x).toBeGreaterThan(addressBox.x + addressBox.width)
  expect(
    Math.abs(helpBox.y + helpBox.height / 2 - addressBox.y - addressBox.height / 2),
  ).toBeLessThan(1)
  await addressHelp.hover()
  await expect(page.locator('.ui-help-content')).toContainText('直接填写原文')
  await page.keyboard.press('Escape')
  const timeoutBox = (await timeout.boundingBox())!
  const retriesBox = (await retries.boundingBox())!
  expect(timeoutBox.x).toBe(before.x)
  expect(timeoutBox.y - (await dialog.boundingBox())!.y).toBe(before.y - dialogBefore.y)
  expect(timeoutBox.y).toBe(proxyBox.y)
  expect(retriesBox.y).toBe(proxyBox.y)
  expect(timeoutBox.x).toBeGreaterThan(proxyBox.x + proxyBox.width)
  expect(retriesBox.x).toBeGreaterThan(timeoutBox.x + timeoutBox.width)
  await proxy.click()
  await page.getByRole('option', { name: '直连', exact: true }).click()
  await expect(address).toHaveCount(0)
  await proxy.click()
  await page.getByRole('option', { name: '自定义代理', exact: true }).click()
  await expect(address).toHaveValue('socks5h://127.0.0.1:1080')
  await address.fill('invalid')
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(address).toHaveAttribute('aria-invalid', 'true')
  await expect(dialog.getByRole('alert')).toContainText('代理地址')
  expect(fixture.calls.filter((call) => call.method !== 'GET')).toEqual([])
  for (const width of [1024, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 })
    expect(
      await dialog.locator('.ui-dialog-body').evaluate((el) => el.scrollWidth - el.clientWidth),
    ).toBeLessThanOrEqual(1)
    await address.scrollIntoViewIfNeeded()
    await expect(address).toBeInViewport()
    await dialog.getByRole('switch', { name: '安全脱敏', exact: true }).scrollIntoViewIfNeeded()
    await expect(dialog.getByRole('switch', { name: '安全脱敏', exact: true })).toBeInViewport()
  }
  expect(fixture.unexpected).toEqual([])
})

test('代理认证分开填写并编码保存，重开表单可回填且恢复原值不提示', async ({ page }) => {
  const fixture = await mockAdmin(page)
  const original = 'http://user%3Aname:p%2fss@proxy.example:8080'
  fixture.provider.proxy_mode = 'custom'
  fixture.provider.proxy = original
  const saves: { proxy: string }[] = []
  await page.route('**/api/providers/main', async (route) => {
    const body = route.request().postDataJSON()
    saves.push(body)
    Object.assign(fixture.provider, body)
    await route.fulfill({ json: fixture.provider })
  })
  await page.goto('/')
  const edit = page.getByRole('button', { name: '编辑 Provider main', exact: true })
  await edit.click()
  const dialog = page.getByRole('dialog', { name: '编辑 Provider', exact: true })
  const address = dialog.getByRole('textbox', { name: '代理地址', exact: true })
  const popup = page.getByRole('dialog', { name: '代理认证', exact: true })
  const username = popup.getByRole('textbox', { name: '代理用户名', exact: true })
  const password = popup.getByRole('textbox', { name: '代理密码', exact: true })
  const auth = dialog.getByRole('button', { name: '认证', exact: true })
  await expect(address).toHaveValue('http://proxy.example:8080')
  const before = (await dialog.boundingBox())!
  await auth.click()
  await expect(username).toBeFocused()
  await expect(username).toHaveValue('user:name')
  await expect(password).toHaveValue('p/ss')
  await expect(password).toHaveClass(/ui-secret-mask/)
  await password.fill('temporary')
  await password.fill('p/ss')
  await popup.getByRole('button', { name: '完成', exact: true }).click()
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByRole('alertdialog')).toHaveCount(0)
  await edit.click()
  const dialogElement = (await dialog.elementHandle())!
  await auth.click()
  await username.fill('用:户@')
  await password.fill('p@ss/#?%40')
  await page.keyboard.press('Escape')
  await expect(popup).toHaveCount(0)
  await expect(dialog).toBeVisible()
  await expect(page.getByRole('dialog', { name: '保存修改？' })).toHaveCount(0)
  await auth.click()
  await expect(password).toHaveValue('p@ss/#?%40')
  await expect(username).toHaveValue('用:户@')
  expect((await dialogElement.boundingBox())!.height).toBe(before.height)
  await page.mouse.click(4, 80)
  await expect(popup).toHaveCount(0)
  await expect(dialog).toBeVisible()
  await expect(page.getByRole('dialog', { name: '保存修改？' })).toHaveCount(0)
  await auth.click()
  await popup.getByRole('button', { name: '完成', exact: true }).click()
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  expect(saves[0]?.proxy).toBe(
    'http://%E7%94%A8%3A%E6%88%B7%40:p%40ss%2F%23%3F%2540@proxy.example:8080',
  )
  await edit.click()
  await auth.click()
  await expect(password).toHaveValue('p@ss/#?%40')
  await expect(username).toHaveValue('用:户@')
  await popup.getByRole('button', { name: '清除', exact: true }).click()
  await popup.getByRole('button', { name: '完成', exact: true }).click()
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  expect(saves[1]?.proxy).toBe('http://proxy.example:8080')
  expect(fixture.unexpected).toEqual([])
})

test('行内开关支持标签点击和键盘，代理及开关保存后同步地址徽标', async ({ page }) => {
  const fixture = await mockAdmin(page)
  const saves: unknown[] = []
  await page.route('**/api/providers/main', async (route) => {
    const body = route.request().postDataJSON()
    saves.push(body)
    Object.assign(fixture.provider, body)
    await route.fulfill({ json: fixture.provider })
  })
  await page.goto('/')
  const edit = page.getByRole('button', { name: '编辑 Provider main', exact: true })
  await edit.click()
  const dialog = page.getByRole('dialog', { name: '编辑 Provider', exact: true })
  for (const name of ['证书校验', '强制上游流式', '安全脱敏']) {
    const field = dialog.locator('.ui-field').filter({
      has: page.getByRole('switch', { name, exact: true }),
    })
    const toggle = field.getByRole('switch', { name, exact: true })
    await field.locator('label').click()
    await expect(toggle).toBeChecked()
    const labelBox = (await field.locator('label').boundingBox())!
    const toggleBox = (await toggle.boundingBox())!
    expect(
      Math.abs(labelBox.y + labelBox.height / 2 - toggleBox.y - toggleBox.height / 2),
    ).toBeLessThanOrEqual(1)
    expect(toggleBox.x).toBeGreaterThan(labelBox.x + labelBox.width)
    await field.getByRole('button', { name: `${name}说明`, exact: true }).hover()
    await expect(page.locator('.ui-help-content')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(toggle).toBeChecked()
  }
  await dialog.getByRole('textbox', { name: 'Hook', exact: true }).fill('routing/main.py')
  await dialog.getByRole('combobox', { name: '连接方式', exact: true }).click()
  await page.getByRole('option', { name: '自定义代理', exact: true }).click()
  await dialog.getByRole('textbox', { name: '代理地址', exact: true }).fill('http://127.0.0.1:7890')
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  expect(saves[0]).toMatchObject({
    proxy_mode: 'custom',
    proxy: 'http://127.0.0.1:7890',
    verify_ssl: true,
    force_upstream_stream: true,
    safe_desensitization_enabled: true,
    hook: 'routing/main.py',
  })
  await expect(page.locator('.provider-metadata .ui-badge')).toHaveText([
    '证书校验',
    '强制上游流式',
    '安全脱敏',
    'Hook: routing/main.py',
  ])
  await edit.click()
  for (const name of ['证书校验', '强制上游流式', '安全脱敏']) {
    const toggle = dialog.getByRole('switch', { name, exact: true })
    await expect(toggle).toBeChecked()
    await toggle.focus()
    await page.keyboard.press('Space')
    await expect(toggle).not.toBeChecked()
  }
  await dialog.getByRole('textbox', { name: 'Hook', exact: true }).fill('')
  await dialog.getByRole('combobox', { name: '连接方式', exact: true }).click()
  await page.getByRole('option', { name: '直连', exact: true }).click()
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  expect(saves[1]).toMatchObject({
    proxy_mode: 'direct',
    proxy: '',
    verify_ssl: false,
    force_upstream_stream: false,
    safe_desensitization_enabled: false,
    hook: '',
  })
  await expect(page.locator('.provider-metadata')).toHaveCount(0)
  expect(fixture.unexpected).toEqual([])
})
