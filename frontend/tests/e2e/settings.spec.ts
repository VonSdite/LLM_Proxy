import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

for (const mode of ['direct', 'proxy', 'external']) {
  test(`监听保存后确认再跳转：${mode}`, async ({ page }) => {
    const fixture = await mockAdmin(page)
    const target = 'http://127.0.0.1:19090/settings'
    await page.route(target, (route) =>
      route.fulfill({ contentType: 'text/html', body: '<h1>New listener</h1>' }),
    )
    await page.route('**/api/settings/system/basic', async (route) => {
      Object.assign(fixture.settings, route.request().postDataJSON())
      await route.fulfill({
        json: {
          settings: fixture.settings,
          server_listener_changed: mode !== 'external',
          server_restart_required: mode === 'external',
          server_access_url: mode === 'direct' ? target : null,
        },
      })
    })
    await page.goto('/settings')
    const original = page.url()
    await page.getByLabel('端口', { exact: true }).fill('19090')
    await page.getByLabel('端口', { exact: true }).press('Tab')
    const dialog = page.getByRole('dialog', {
      name: mode === 'external' ? '监听配置已保存' : '监听已更新',
      exact: true,
    })
    await expect(dialog).toBeVisible()
    await expect(page).toHaveURL(original)
    await expect(dialog.getByRole('textbox')).toHaveValue(
      mode === 'direct' ? target : '127.0.0.1:19090',
    )
    await page.keyboard.press('Escape')
    await page.mouse.click(5, 5)
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: mode === 'direct' ? '前往新地址' : '知道了' }).click()
    if (mode === 'direct') {
      await expect(page).toHaveURL(target)
      await expect(page.getByRole('heading', { name: 'New listener' })).toBeVisible()
    } else {
      await expect(dialog).toHaveCount(0)
      await expect(page).toHaveURL(original)
    }
  })
}

test('监听绑定失败保留草稿，重试成功后再显示新地址', async ({ page }) => {
  const fixture = await mockAdmin(page)
  let attempts = 0
  await page.route('**/api/settings/system/basic', async (route) => {
    if (++attempts === 1) {
      await route.fulfill({
        status: 400,
        json: { error: 'Cannot bind new listener; current listener is unchanged' },
      })
    } else {
      Object.assign(fixture.settings, route.request().postDataJSON())
      await route.fulfill({
        json: {
          settings: fixture.settings,
          server_listener_changed: true,
          server_access_url: 'http://127.0.0.1:19090/settings',
        },
      })
    }
  })
  await page.goto('/settings')
  await page.getByLabel('端口', { exact: true }).fill('19090')
  await page.getByLabel('端口', { exact: true }).press('Tab')
  await expect(
    page.locator('.ui-toast').filter({ hasText: '监听未保存：新监听不可用' }),
  ).toBeVisible()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByLabel('端口', { exact: true })).toHaveValue('19090')
  expect(fixture.settings.server.port).toBe(9090)
  await page.getByLabel('端口', { exact: true }).focus()
  await page.getByRole('heading', { name: '系统设置' }).click()
  await expect(page.getByRole('dialog', { name: '监听已更新', exact: true })).toBeVisible()
  expect(attempts).toBe(2)
})

for (const enabled of [true, false]) {
  test(`管理登录修改后等待确认再刷新，登录${enabled ? '启用' : '关闭'}`, async ({ page }) => {
    const fixture = await mockAdmin(page)
    await page.route('**/api/settings/system/basic', async (route) => {
      Object.assign(fixture.settings, route.request().postDataJSON())
      fixture.settings.auth_enabled = enabled
      fixture.bootstrap.auth_enabled = enabled
      fixture.bootstrap.authenticated = false
      await route.fulfill({ json: { settings: fixture.settings, auth_config_changed: true } })
    })
    await page.goto('/settings')
    await page.getByLabel('管理账号', { exact: true }).fill('new-admin')
    await page.getByLabel('管理密码', { exact: true }).fill(enabled ? 'new-password' : '')
    await page.getByRole('heading', { name: '系统设置' }).click()
    const dialog = page.getByRole('dialog', { name: '登录信息已修改', exact: true })
    await expect(dialog).toBeVisible()
    await expect(page).toHaveURL(/\/settings$/)
    await expect(dialog.getByLabel('已保存账号')).toHaveValue('new-admin')
    const password = dialog.getByLabel('已保存密码')
    await expect(password).toHaveValue(enabled ? 'new-password' : '')
    await expect(password).toHaveAttribute('readonly', '')
    await expect(password).toHaveClass(/ui-secret-mask/)
    await dialog.getByRole('button', { name: '显示内容', exact: true }).click()
    await expect(password).not.toHaveClass(/ui-secret-mask/)
    await page.keyboard.press('Escape')
    await page.mouse.click(5, 5)
    await expect(dialog).toBeVisible()
    await expect(dialog.getByRole('button', { name: '关闭', exact: true })).toHaveCount(0)
    await expect(page).toHaveURL(/\/settings$/)
    await dialog.getByRole('button', { name: '已记住，继续', exact: true }).click()
    await expect(dialog).toHaveCount(0)
    await expect(page).toHaveURL(enabled ? /\/login$/ : /\/settings$/)
    expect(fixture.unexpected).toEqual([])
  })
}

test('管理登录无变化不保存，保存失败不弹成功预览', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/settings')
  const account = page.getByLabel('管理账号', { exact: true })
  await account.fill('temporary')
  await account.fill('admin')
  await page.getByRole('heading', { name: '系统设置' }).click()
  expect(fixture.calls.filter((call) => call.method === 'PUT')).toHaveLength(0)
  await page.route('**/api/settings/system/basic', (route) =>
    route.fulfill({ status: 500, json: { error: 'Save failed' } }),
  )
  await account.fill('new-admin')
  await page.getByRole('heading', { name: '系统设置' }).click()
  await expect(page.getByText('未保存：Save failed')).toBeVisible()
  await expect(page.getByRole('dialog', { name: '登录信息已修改' })).toHaveCount(0)
  await expect(account).toHaveValue('new-admin')
  await expect(page).toHaveURL(/\/settings$/)
})

test('系统设置仅保留通用分组，启用客户端 IP 不拉高日志卡片', async ({ page }) => {
  await mockAdmin(page)
  await page.goto('/settings')
  await expect(page.getByRole('form')).toHaveCount(5)
  await expect(page.getByRole('switch', { name: '启用 API Key', exact: true })).toHaveCount(0)
  await expect(page.getByRole('switch', { name: '启用模型映射', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '网络设置', exact: true })).toHaveCount(0)
  const logs = page.getByRole('form', { name: '日志与调试', exact: true })
  const height = (await logs.boundingBox())!.height
  await page.getByRole('switch', { name: '使用代理转发的 IP', exact: true }).click()
  await expect(page.getByLabel('IP 请求头', { exact: true })).toBeVisible()
  expect((await logs.boundingBox())!.height).toBe(height)
})

test('功能关闭时内容置灰且鼠标键盘不可操作，重新开启恢复，模型映射入口常驻', async ({ page }) => {
  const fixture = await mockAdmin(page)
  fixture.bootstrap.features.oauth = false
  fixture.bootstrap.features.api_keys = false
  fixture.bootstrap.features.model_mapping = false
  fixture.settings.oauth.enabled = false
  fixture.settings.api_keys.enabled = false
  for (const [path, label] of [
    ['/api-keys', '启用 API Key'],
    ['/oauth', '启用 OAuth'],
  ]) {
    await page.goto(path!)
    const toggle = page.getByRole('switch', { name: label!, exact: true })
    await expect(toggle).not.toBeChecked()
    await expect(page.getByRole('navigation', { name: '主导航' }).getByRole('link')).toHaveCount(8)
    const content = page.locator('.ui-disabled-region')
    await expect(content).toHaveAttribute('inert', '')
    await expect(content).toHaveCSS('opacity', '0.5')
    const add = content.getByRole('button', {
      name: path === '/oauth' ? '添加账号' : '创建 API Key',
      exact: true,
      includeHidden: true,
    })
    const position = (await add.boundingBox())!
    await page.mouse.click(position.x + position.width / 2, position.y + position.height / 2)
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await toggle.focus()
    await page.keyboard.press('Tab')
    expect(await content.evaluate((element) => element.contains(document.activeElement))).toBe(
      false,
    )
    await toggle.click()
    await expect
      .poll(() =>
        path === '/oauth' ? fixture.settings.oauth.enabled : fixture.settings.api_keys.enabled,
      )
      .toBe(true)
    await expect(content).not.toHaveAttribute('inert')
    await expect(content).toHaveCSS('opacity', '1')
    await add.click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await page.reload()
    await expect(toggle).toBeChecked()
  }
  await page.goto('/model-mappings')
  await expect(page.getByRole('heading', { name: '模型映射', exact: true })).toBeVisible()
  expect(fixture.unexpected).toEqual([])
})

test('监听与账号离开各自编辑区域后独立保存，组内切换不提交', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/settings')
  await expect(page.getByRole('button', { name: '保存', exact: true })).toHaveCount(0)
  await page.getByLabel('监听地址', { exact: true }).fill('0.0.0.0')
  await page.getByLabel('端口', { exact: true }).fill('10003')
  expect(fixture.calls.filter((call) => call.method === 'PUT')).toEqual([])
  await page.getByRole('heading', { name: '系统设置' }).click()
  await expect.poll(() => fixture.settings.server.port).toBe(10003)
  expect(fixture.calls.filter((call) => call.method === 'PUT')[0]?.body).toEqual({
    server: { host: '0.0.0.0', port: 10003 },
  })
  await page.getByLabel('管理账号', { exact: true }).fill('new-admin')
  await page.getByLabel('管理密码', { exact: true }).fill('new-password')
  expect(fixture.calls.filter((call) => call.method === 'PUT')).toHaveLength(1)
  await page.getByRole('heading', { name: '系统设置' }).click()
  await expect
    .poll(() => fixture.calls.filter((call) => call.method === 'PUT').map((call) => call.body))
    .toEqual([
      { server: { host: '0.0.0.0', port: 10003 } },
      { admin: { username: 'new-admin', password: 'new-password' } },
    ])
  await expect(page.getByLabel('监听地址', { exact: true })).toHaveValue('0.0.0.0')
  await page.getByLabel('端口', { exact: true }).focus()
  await page.getByRole('heading', { name: '系统设置' }).click()
  expect(fixture.calls.filter((call) => call.method === 'PUT')).toHaveLength(2)
})

test('监听格式错误显示在字段下，修正后自动保存', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/settings')
  const host = page.getByLabel('监听地址', { exact: true })
  const port = page.getByLabel('端口', { exact: true })
  await host.fill('0.')
  await page.getByRole('heading', { name: '系统设置' }).click()
  await expect(page.getByText('请输入有效的 IPv4 或 IPv6 地址', { exact: true })).toBeVisible()
  await expect(host).toHaveAttribute('aria-invalid', 'true')
  await expect(page.locator('.setting-error')).toHaveCount(0)
  expect(fixture.calls.filter((call) => call.method === 'PUT')).toHaveLength(0)
  await host.fill('0.0.0.0')
  await port.fill('70000')
  await page.getByRole('heading', { name: '系统设置' }).click()
  await expect(page.getByText('端口范围为 1–65535', { exact: true })).toBeVisible()
  expect(fixture.calls.filter((call) => call.method === 'PUT')).toHaveLength(0)
  await port.fill('10003')
  await port.press('Enter')
  await expect.poll(() => fixture.settings.server.port).toBe(10003)
  await expect(page.locator('.ui-field-error')).toHaveCount(0)
  expect(fixture.calls.filter((call) => call.method === 'PUT')).toHaveLength(1)
})

test('客户端 IP 输入仅启用时显示，文本结束编辑保存，非法输入不阻止关闭', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/settings')
  const header = page.getByLabel('IP 请求头', { exact: true })
  const enabled = page.getByRole('switch', { name: '使用代理转发的 IP', exact: true })
  await expect(header).toHaveCount(0)
  await enabled.click()
  await expect(header).toBeVisible()
  await expect.poll(() => fixture.settings.client_ip.real_ip_enabled).toBe(true)
  await header.fill('X-Forwarded-For')
  expect(fixture.settings.client_ip.real_ip_header).toBe('X-Real-IP')
  await header.press('Tab')
  await expect.poll(() => fixture.settings.client_ip.real_ip_header).toBe('X-Forwarded-For')
  await header.fill('invalid header')
  await header.press('Tab')
  await expect(page.getByText('请输入合法的 HTTP 请求头名称')).toBeVisible()
  await enabled.click()
  await expect(header).toHaveCount(0)
  await expect.poll(() => fixture.settings.client_ip.real_ip_enabled).toBe(false)
  expect(fixture.settings.client_ip.real_ip_header).toBe('X-Forwarded-For')
})

test('OAuth 网络弹窗连续开关合并为最新值，在途响应不覆盖输入', async ({ page }) => {
  const fixture = await mockAdmin(page)
  let release!: () => void
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  const sent: unknown[] = []
  await page.route('**/api/settings/system/oauth', async (route) => {
    const body = route.request().postDataJSON()
    sent.push(body)
    if (sent.length === 1) await gate
    Object.assign(fixture.settings, body)
    await route.fulfill({ json: { settings: fixture.settings } })
  })
  await page.goto('/oauth')
  await page.getByRole('button', { name: '网络设置', exact: true }).click()
  const tls = page.getByRole('switch', { name: '验证 TLS 证书', exact: true })
  await tls.click()
  await expect.poll(() => sent.length).toBe(1)
  await tls.click()
  await page.getByRole('switch', { name: '安全脱敏', exact: true }).click()
  await expect(tls).not.toBeChecked()
  release()
  await expect.poll(() => sent.length).toBe(2)
  await expect.poll(() => fixture.settings.oauth.safe_desensitization_enabled).toBe(true)
  expect(fixture.settings.oauth.verify_ssl).toBe(false)
  await expect(tls).not.toBeChecked()
  expect(fixture.unexpected).toEqual([])
})

test('自动保存失败保留修改并允许重试', async ({ page }) => {
  const fixture = await mockAdmin(page)
  let attempts = 0
  await page.route('**/api/settings/system/api-keys', async (route) => {
    attempts++
    if (attempts === 1) {
      await route.fulfill({ status: 500, json: { error: 'Save failed' } })
    } else {
      Object.assign(fixture.settings, route.request().postDataJSON())
      await route.fulfill({ json: { settings: fixture.settings } })
    }
  })
  await page.goto('/api-keys')
  const enabled = page.getByRole('switch', { name: '启用 API Key', exact: true })
  await enabled.click()
  await expect(page.getByText('未保存：Save failed')).toBeVisible()
  await expect(enabled).not.toBeChecked()
  await enabled.click()
  await expect(page.getByText('未保存：Save failed')).toHaveCount(0)
  expect(attempts).toBe(1)
  attempts = 0
  await enabled.click()
  await expect(page.getByText('未保存：Save failed')).toBeVisible()
  await page.getByRole('button', { name: '重试', exact: true }).click()
  await expect.poll(() => fixture.settings.api_keys.enabled).toBe(false)
  await expect(page.getByText('未保存：Save failed')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '刷新 API Key', exact: true })).toBeDisabled()
})

test('OAuth 网络弹窗收起时提交当前输入且保留账号列表', async ({ page }) => {
  const fixture = await mockAdmin(page)
  fixture.settings.oauth.proxy_mode = 'custom'
  await page.goto('/oauth')
  await page.getByRole('button', { name: '网络设置', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'OAuth 网络设置', exact: true })
  await dialog
    .getByRole('textbox', { name: '代理地址', exact: true })
    .fill('http://proxy.example:8080')
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeVisible()
  await expect.poll(() => fixture.settings.oauth.proxy).toBe('http://proxy.example:8080')
  await page.getByRole('button', { name: '网络设置', exact: true }).click()
  await expect(dialog.getByRole('textbox', { name: '代理地址', exact: true })).toHaveValue(
    'http://proxy.example:8080',
  )
  expect(
    fixture.calls.filter((call) => call.path === '/api/settings/system' && call.method === 'GET'),
  ).toHaveLength(1)
})

test('日志目录结束编辑、级别选择后自动保存', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/settings')
  const directory = page.getByLabel('日志目录', { exact: true })
  await directory.fill('logs/custom')
  expect(fixture.settings.logging.path).toBe('logs')
  await directory.press('Tab')
  await expect.poll(() => fixture.settings.logging.path).toBe('logs/custom')
  await page.getByRole('combobox', { name: '日志级别', exact: true }).click()
  await page.getByRole('option', { name: 'WARNING', exact: true }).click()
  await expect.poll(() => fixture.settings.logging.level).toBe('WARNING')
})
