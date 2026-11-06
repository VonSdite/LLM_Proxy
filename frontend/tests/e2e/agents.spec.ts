import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'
import type { AgentMachine, PreviewInput } from '../../src/api/agents'

test('SSH 必填字段标红，代理、主机指纹、连接与断开使用当前机器', async ({ page }) => {
  const fixture = await mockAdmin(page)
  const machine: AgentMachine = {
    id: 'remote',
    name: 'Linux 开发机',
    host: '192.0.2.10',
    port: '2222',
    user: 'dev',
    local: false,
    connected: false,
    platform: 'Linux',
    proxy: { mode: 'custom', url: 'socks5h://127.0.0.1:1080' },
  }
  let saved: unknown
  const trusted: unknown[] = []
  await page.route('**/api/agent-config/machines', async (route) => {
    saved = route.request().postDataJSON()
    expect(route.request().headers()['x-agent-config']).toBe('1')
    await route.fulfill({ json: { machine } })
  })
  await page.route('**/api/agent-config/machines/remote/connect', async (route) => {
    trusted.push(route.request().postDataJSON())
    await route.fulfill(
      trusted.length === 1
        ? {
            status: 409,
            json: {
              error: 'Host key confirmation required',
              code: 'host_key_required',
              fingerprint: 'SHA256:fixture-host-key',
              key_type: 'ssh-ed25519',
            },
          }
        : { json: { ...fixture.snapshot, machine: { ...machine, connected: true } } },
    )
  })
  await page.route('**/api/agent-config/machines/remote/disconnect', (route) =>
    route.fulfill({ json: {} }),
  )
  await page.route('**/api/agent-config/machines/remote', (route) => route.fulfill({ json: {} }))
  await page.goto('/agents')
  await page.getByRole('button', { name: '添加 SSH 机器', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '添加 SSH 机器' })
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  const host = dialog.getByRole('textbox', { name: 'IP / 主机名', exact: true })
  await expect(host).toHaveAttribute('aria-invalid', 'true')
  await expect(host).toBeFocused()
  await expect(dialog.getByRole('textbox', { name: 'SSH 用户', exact: true })).toHaveAttribute(
    'aria-invalid',
    'true',
  )
  await expect(dialog.locator('.ui-field-error svg').first()).toHaveCSS(
    'color',
    'rgb(255, 140, 144)',
  )
  await dialog.getByRole('textbox', { name: '名称', exact: true }).fill(machine.name)
  await host.fill(machine.host)
  await dialog.getByRole('textbox', { name: '端口', exact: true }).fill(machine.port)
  await dialog.getByRole('textbox', { name: 'SSH 用户', exact: true }).fill(machine.user)
  await dialog.getByRole('textbox', { name: 'SSH 密码', exact: true }).fill('session-only-password')
  await dialog.getByRole('combobox', { name: '连接方式', exact: true }).click()
  await page.getByRole('option', { name: '自定义代理', exact: true }).click()
  await dialog.getByRole('textbox', { name: '代理地址', exact: true }).fill(machine.proxy.url)
  await dialog.locator('footer').getByRole('button', { name: '保存并连接', exact: true }).click()
  const fingerprint = page.getByRole('dialog', { name: '确认 SSH 主机' })
  await expect(fingerprint).toContainText('SHA256:fixture-host-key')
  await expect(page.getByText('连接后编辑配置')).toBeVisible()
  await fingerprint.getByRole('button', { name: '信任并连接' }).click()
  await expect(page.getByRole('combobox', { name: 'API 地址', exact: true })).toBeVisible()
  expect(saved).toMatchObject({
    host: machine.host,
    port: machine.port,
    user: 'dev',
    password: 'session-only-password',
    proxy: machine.proxy,
  })
  expect(trusted).toEqual([{}, { trustFingerprint: 'SHA256:fixture-host-key' }])
  await page.getByRole('button', { name: '断开', exact: true }).click()
  await expect(page.getByText('连接后编辑配置')).toBeVisible()
  await page.getByRole('button', { name: '删除机器', exact: true }).click()
  await page.getByRole('button', { name: '确认删除', exact: true }).click()
  await expect(page.getByRole('button', { name: /本机 Windows/ })).toHaveAttribute(
    'aria-pressed',
    'true',
  )
})

for (const protocol of ['http', 'https']) {
  test(`SSH 保存 ${protocol.toUpperCase()} 代理且密码留空可使用密钥`, async ({ page }) => {
    const fixture = await mockAdmin(page)
    let saved: unknown
    await page.route('**/api/agent-config/machines', async (route) => {
      saved = route.request().postDataJSON()
      await route.fulfill({
        json: {
          machine: {
            ...fixture.snapshot.machine,
            id: 'remote',
            name: 'remote',
            local: false,
            connected: false,
          },
        },
      })
    })
    await page.goto('/agents')
    await page.getByRole('button', { name: '添加 SSH 机器', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('textbox', { name: 'IP / 主机名', exact: true }).fill('192.0.2.20')
    await dialog.getByRole('textbox', { name: 'SSH 用户', exact: true }).fill('dev')
    await dialog.getByRole('combobox', { name: '连接方式', exact: true }).click()
    await page.getByRole('option', { name: '自定义代理', exact: true }).click()
    await dialog
      .getByRole('textbox', { name: '代理地址', exact: true })
      .fill(`${protocol}://127.0.0.1:8088`)
    await dialog.getByRole('button', { name: '保存', exact: true }).click()
    await expect(dialog).not.toBeVisible()
    expect(saved).toMatchObject({
      password: '',
      proxy: { mode: 'custom', url: `${protocol}://127.0.0.1:8088` },
    })
    await expect(page.getByText('连接后编辑配置')).toBeVisible()
  })
}

test('API Key 候选在移入和停留时稳定，可选已有凭据也可手填', async ({ page }) => {
  await mockAdmin(page)
  await page.goto('/agents')
  const key = page.getByRole('combobox', { name: 'API Key', exact: true })
  const address = page.getByRole('combobox', { name: 'API 地址', exact: true })
  for (const input of [key, address]) {
    await expect(input).toHaveAttribute('autocomplete', 'new-password')
    await expect(input).toHaveAttribute('type', 'text')
  }
  await key.focus()
  const candidate = page.getByRole('option', { name: /开发 Key/ })
  await expect(candidate).toBeVisible()
  await candidate.hover()
  await page.clock.install()
  await page.clock.runFor(600)
  await expect(candidate).toBeVisible()
  await expect(page.getByRole('listbox')).toHaveCSS('background-color', 'rgb(44, 44, 48)')
  await candidate.click()
  await expect(key).toHaveValue('fixture-key')
  await key.fill('manual-api-key')
  await key.press('Tab')
  await expect(key).toHaveValue('manual-api-key')
  await address.fill('https://external.example/v1')
  await address.press('Tab')
  await expect(page.getByRole('textbox', { name: 'API Key', exact: true })).toHaveValue(
    'manual-api-key',
  )
  await expect(page.getByRole('textbox', { name: 'ANTHROPIC_MODEL', exact: true })).toBeVisible()
})

test('路径文字独立命中，三种 Agent 自定义路径刷新后保留，Codex 两文件关联', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.route('**/api/agent-config/machines/local/path', async (route) => {
    const { target, path } = route.request().postDataJSON() as {
      target: 'claude' | 'codex' | 'opencode'
      path: string
    }
    fixture.snapshot.files[target].customPath = path
    fixture.snapshot.files[target].path = target === 'codex' ? `${path}/config.toml` : path
    if (target === 'codex') fixture.snapshot.files.codexAuth.path = `${path}/auth.json`
    await route.fulfill({ json: fixture.snapshot })
  })
  await page.goto('/agents')
  const row = page.locator('.config-path-row').first()
  await expect(row).toBeVisible()
  const box = (await row.boundingBox())!
  const path = (await row.locator('.config-path-link').boundingBox())!
  expect(path.width).toBeLessThan(box.width - 150)
  await page.mouse.click(box.x + box.width - 90, box.y + box.height / 2)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  for (const item of [
    {
      tab: 'Claude',
      file: 'settings.json',
      title: 'Claude 配置文件',
      label: '文件路径',
      value: 'D:/custom/settings.json',
    },
    {
      tab: 'Codex',
      file: 'config.toml',
      title: 'Codex 配置目录',
      label: '目录路径',
      value: 'D:/custom/codex',
    },
    {
      tab: 'opencode',
      file: 'opencode.json',
      title: 'opencode 配置文件',
      label: '文件路径',
      value: 'D:/custom/opencode.jsonc',
    },
  ]) {
    await page.getByRole('tab', { name: item.tab, exact: true }).click()
    await page.getByRole('button', { name: `${item.file} 路径设置`, exact: true }).click()
    const dialog = page.getByRole('dialog', { name: item.title, exact: true })
    await dialog.getByRole('textbox', { name: item.label, exact: true }).fill(item.value)
    await dialog.getByRole('button', { name: '保存', exact: true }).click()
    await expect(dialog).not.toBeVisible()
    await page.reload()
    await page.getByRole('tab', { name: item.tab, exact: true }).click()
    await expect(
      page.getByRole('button', {
        name: item.tab === 'Codex' ? `${item.value}/config.toml` : item.value,
        exact: true,
      }),
    ).toBeVisible()
    if (item.tab === 'Codex')
      await expect(
        page.getByRole('button', { name: `${item.value}/auth.json`, exact: true }),
      ).toBeVisible()
  }
})

test('已有 Claude 缺失项显示不设置，新建文件使用约定默认值，沙箱三态可保存', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/agents')
  const sandbox = page.getByRole('combobox', { name: '命令沙箱', exact: true })
  await expect(sandbox).toHaveText('不设置')
  await expect(page.getByRole('combobox', { name: 'Attribution Header', exact: true })).toHaveText(
    '不设置',
  )
  fixture.snapshot.files.claude.exists = false
  await page.reload()
  for (const [name, value] of [
    ['命令沙箱', '不设置'],
    ['Attribution Header', '禁用'],
    ['非必要流量', '禁用'],
    ['实验性 Agent Teams', '启用'],
    ['Tool Search', '启用'],
    ['隔离环境标记', '启用'],
    ['跳过 WebFetch 预检', '启用'],
  ])
    await expect(page.getByRole('combobox', { name, exact: true })).toHaveText(value!)
  for (const [label, state] of [
    ['启用', 'enabled'],
    ['禁用', 'disabled'],
    ['不设置', 'unset'],
  ]) {
    await sandbox.click()
    await page.getByRole('option', { name: label, exact: true }).click()
    await expect(sandbox).toHaveText(label!)
    await expect
      .poll(
        () =>
          (fixture.calls.filter((c) => c.path.endsWith('/preview')).at(-1)?.body as PreviewInput)
            ?.draft?.runtime.sandboxEnabled,
      )
      .toBe(state)
  }
})

test('参数候选包含多个参考模型和来源，问号悬停并可移入', async ({ page }) => {
  const fixture = await mockAdmin(page)
  fixture.snapshot.files.claude.managed = {
    'env.ANTHROPIC_DEFAULT_HAIKU_MODEL': 'main/unknown-model',
    'env.ANTHROPIC_DEFAULT_OPUS_MODEL': 'main/sonnet-alias',
  }
  fixture.agent.modelLimitPresets[0]!.aliases = ['sonnet-alias']
  fixture.agent.providers.push({
    key: 'main',
    name: 'main',
    enabled: true,
    models: [{ id: 'main/sonnet-alias', contextWindow: 240000 }],
  })
  await page.goto('/agents')
  const field = page
    .locator('.ui-field')
    .filter({ has: page.getByRole('textbox', { name: '压缩窗口', exact: true }) })
  await field.getByRole('button', { name: '参数参考值' }).click()
  const options = page.locator('.ui-number-options')
  await expect(options).toContainText('main/unknown-model · 通用参考值')
  await expect(options).toContainText('main/sonnet-alias · Provider 设置')
  await options.getByRole('button', { name: /240,000/ }).click()
  await expect(field.getByRole('textbox')).toHaveValue('240000')
  await page.getByRole('button', { name: '压缩窗口说明', exact: true }).hover()
  const help = page.locator('.ui-help-content')
  await expect(help).toContainText('CLAUDE_CODE_AUTO_COMPACT_WINDOW')
  await help.hover()
  await expect(help).toHaveCSS('background-color', 'rgb(44, 44, 48)')
  await page.keyboard.press('Escape')
  await expect(help).not.toBeVisible()
})

for (const agent of ['codex', 'opencode'] as const) {
  test(`${agent} 保留精确压缩 Token，数值改变后失焦刷新标题`, async ({ page }) => {
    const fixture = await mockAdmin(page)
    fixture.snapshot.files.codex.managed = {
      model: 'main/claude-sonnet-4-6',
      model_context_window: 200000,
      model_auto_compact_token_limit: 123457,
    }
    fixture.snapshot.files.opencode.managed = {
      model: 'main/main/claude-sonnet-4-6',
      'provider.main.models.main/claude-sonnet-4-6': { limit: { context: 200000, output: 64000 } },
      'compaction.reserved': 76543,
    }
    await page.goto('/agents')
    await page
      .getByRole('tab', { name: agent === 'codex' ? 'Codex' : 'opencode', exact: true })
      .click()
    const compact = page.getByRole('textbox', { name: '压缩阈值 %', exact: true })
    const label = page.locator('.ui-field-label').filter({ hasText: '压缩阈值 %' })
    const initial = agent === 'codex' ? '上限 123,457' : '预留 76,543'
    await expect(label).toContainText(initial)
    await compact.focus()
    await compact.press('Tab')
    await expect(label).toContainText(initial)
    const preview = () =>
      (
        fixture.calls.filter((c) => c.path.endsWith(`/${agent}/preview`)).at(-1)
          ?.body as PreviewInput
      )?.draft
    await expect
      .poll(() => preview()?.limits)
      .toMatchObject(agent === 'codex' ? { compactTokens: 123457 } : { reserved: 76543 })
    await compact.fill('70')
    await expect(label).toContainText(initial)
    await compact.press('Tab')
    await expect(label).toContainText(agent === 'codex' ? '上限 140,000' : '预留 60,000')
    await expect.poll(() => preview()?.limits.compact).toBe(70)
    expect(preview()?.limits.compactTokens).toBeUndefined()
    expect(preview()?.limits.reserved).toBeUndefined()
  })
}

test('文件高亮、备份、空白关闭保存及冲突后保留内容', async ({ page }) => {
  const fixture = await mockAdmin(page)
  let attempts = 0
  let raw: PreviewInput | undefined
  await page.route('**/api/agent-config/machines/local/claude/files?*', async (route) => {
    const backup = new URL(route.request().url()).searchParams.get('backup')
    await route.fulfill({
      json: {
        files: [
          {
            target: 'claude',
            path: fixture.snapshot.files.claude.path,
            version: 'original-version',
            exists: true,
            text: JSON.stringify(
              { model: backup === '1' ? 'backup-model' : 'original-model' },
              null,
              2,
            ),
          },
        ],
      },
    })
  })
  await page.route('**/api/agent-config/machines/local/claude/preview', async (route) => {
    const body = route.request().postDataJSON() as PreviewInput
    if (!body.raw) return route.fallback()
    raw = body
    await route.fulfill({ json: { token: 'raw-file-ticket', hasChanges: true, rows: [] } })
  })
  await page.route('**/api/agent-config/machines/local/apply', async (route) => {
    expect(route.request().postDataJSON()).toEqual({ token: 'raw-file-ticket' })
    attempts++
    await route.fulfill(
      attempts === 1
        ? { status: 409, json: { error: '文件已被其他程序修改' } }
        : { json: fixture.snapshot },
    )
  })
  await page.goto('/agents')
  await page.getByRole('button', { name: fixture.snapshot.files.claude.path, exact: true }).click()
  const editor = page.getByRole('dialog', { name: 'settings.json', exact: true })
  await expect(editor.locator('.ace_string').first()).toBeVisible()
  await editor.getByRole('combobox', { name: '读取备份' }).click()
  await page.getByRole('option', { name: '备份 1', exact: true }).click()
  await expect(editor.locator('.ace_content')).toContainText('backup-model')
  await page.mouse.click(12, 120)
  const prompt = page.getByRole('dialog', { name: '保存修改？' })
  await expect(prompt).toBeVisible()
  await prompt.getByRole('button', { name: '保存', exact: true }).click()
  await expect(editor.getByRole('alert')).toContainText('文件已被其他程序修改')
  await expect(editor.locator('.ace_content')).toContainText('backup-model')
  expect(raw?.versions).toEqual({ claude: 'original-version' })
  await editor.getByRole('button', { name: '保存', exact: true }).click()
  await expect(editor).not.toBeVisible()
  expect(attempts).toBe(2)
})

test('连续编辑预览保持 DOM、滚动和焦点，离开配置页无需确认', async ({ page }) => {
  await mockAdmin(page)
  await page.goto('/agents')
  const address = page.getByRole('combobox', { name: 'API 地址', exact: true })
  await address.fill('https://external.example/v1')
  await address.press('Tab')
  const all = page.getByRole('textbox', { name: '一键设置模型', exact: true })
  await all.fill('model')
  await expect(page.getByText('有待保存的修改')).toBeVisible()
  const input = await all.elementHandle()
  const before = await all.boundingBox()
  await all.pressSequentially('-new', { delay: 90 })
  await expect(page.getByText('有待保存的修改')).toBeVisible()
  await expect(all).toBeFocused()
  expect(await all.evaluate((element, old) => element === old, input)).toBe(true)
  expect((await all.boundingBox())?.y).toBe(before?.y)
  const main = page.getByRole('textbox', { name: 'ANTHROPIC_MODEL', exact: true })
  await main.fill('different-model')
  await expect(all).toHaveValue('')
  await page
    .getByRole('navigation', { name: '主导航' })
    .getByRole('link', { name: '设置', exact: true })
    .click()
  await expect(page).toHaveURL(/\/settings$/)
  await expect(page.getByRole('dialog')).toHaveCount(0)
})
