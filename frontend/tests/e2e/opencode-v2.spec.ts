import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'
import { addExtensionFixtures } from '../agent-extension-fixtures'
import type { PreviewInput } from '@/api/agents'

test('Opencode V1 已有分享和更新值正确回显，切回页签读取最新值', async ({ page }) => {
  const fixture = await mockAdmin(page)
  addExtensionFixtures(fixture)
  Object.assign(fixture.snapshot.files.opencode!.managed, {
    share: 'disabled',
    autoupdate: 'notify',
  })
  await page.goto('/agents')
  await page.getByRole('tab', { name: 'Opencode v1', exact: true }).click()
  await expect(page.getByRole('combobox', { name: '会话分享', exact: true })).toHaveText('禁用分享')
  await expect(page.getByRole('combobox', { name: '更新检查', exact: true })).toHaveText('仅通知')
  await page.reload()
  await expect(page.getByRole('tab', { name: 'Opencode v1', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await page.getByRole('tab', { name: 'Claude', exact: true }).click()
  fixture.snapshot.files.opencode!.managed.autoupdate = false
  await page.getByRole('tab', { name: 'Opencode v1', exact: true }).click()
  await expect(page.getByRole('combobox', { name: '更新检查', exact: true })).toHaveText('关闭')
  await expect(page.getByRole('combobox', { name: '会话分享', exact: true })).toHaveText('禁用分享')
})

for (const [agent, label] of [
  ['opencode', 'Opencode v1'],
  ['opencode_v2', 'Opencode v2'],
]) {
  test(`${label} 禁用服务支持选择、手输徽标和移除`, async ({ page }) => {
    const fixture = await mockAdmin(page)
    addExtensionFixtures(fixture)
    await page.goto('/agents')
    await page.getByRole('tab', { name: label, exact: true }).click()
    const input = page.getByRole('combobox', { name: '禁用服务', exact: true })
    const value = () => {
      const draft = (
        fixture.calls.filter((call) => call.path.endsWith(`/${agent}/preview`)).at(-1)
          ?.body as PreviewInput
      )?.draft
      return agent === 'opencode'
        ? draft?.runtime.disabledProviders
        : draft?.values?.['disabled-providers']
    }
    await input.click()
    await page.getByRole('option', { name: 'anthropic', exact: true }).click()
    await expect(page.getByRole('button', { name: '移除 anthropic', exact: true })).toBeVisible()
    await input.fill('my-provider')
    await input.press('Enter')
    await expect(page.getByRole('button', { name: '移除 my-provider', exact: true })).toBeVisible()
    await expect(input).toHaveValue('')
    await expect.poll(value).toBe('anthropic, my-provider')
    await input.fill('my-provider')
    await input.press('Enter')
    await expect(page.getByRole('button', { name: '移除 my-provider', exact: true })).toHaveCount(1)
    await input.press('Escape')
    await page.getByRole('button', { name: '移除 anthropic', exact: true }).click()
    await expect.poll(value).toBe('my-provider')
    await page.getByRole('button', { name: '移除 my-provider', exact: true }).click()
    await expect.poll(value).toBe(agent === 'opencode' ? '' : null)
    expect(fixture.unexpected).toEqual([])
  })
}

test('Opencode V2 回填自定义设置、独立草稿和权限替换', async ({ page }) => {
  const fixture = await mockAdmin(page)
  addExtensionFixtures(fixture)
  Object.assign(fixture.snapshot.files.opencodeV2!, {
    exists: true,
    managed: {
      'base-url': 'https://external.example/v1',
      model: 'upstream/model',
      protocol: '@opencode/ai/providers/openai/responses',
      permission: { custom: true },
      buffer: '20000',
      context: 200000,
    },
  })
  await page.goto('/agents')
  await page.getByRole('tab', { name: 'Opencode v2', exact: true }).click()
  const preview = () =>
    (
      fixture.calls.filter((call) => call.path.endsWith('/opencode_v2/preview')).at(-1)
        ?.body as PreviewInput
    )?.draft
  await expect(page.getByRole('textbox', { name: '默认主模型', exact: true })).toHaveValue(
    'upstream/model',
  )
  await expect(page.getByRole('combobox', { name: 'API 协议', exact: true })).toHaveText(
    '保留自定义配置',
  )
  const permission = page.getByRole('combobox', { name: '操作确认策略', exact: true })
  await page.reload()
  await expect(page.getByRole('tab', { name: 'Opencode v2', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await expect(permission).toHaveText('保留自定义配置')
  const buffer = page.getByRole('textbox', { name: '压缩预留', exact: true })
  await expect(buffer).toHaveValue('20000')
  await buffer.fill('15%')
  await expect.poll(() => preview()?.values?.buffer).toBe('15%')
  expect(preview()?.values).not.toHaveProperty('permission')
  expect(preview()?.values).not.toHaveProperty('protocol')
  await permission.click()
  await page.getByRole('option', { name: '跳过确认', exact: true }).click()
  await expect.poll(() => preview()?.values?.permission).toBe('allow')
  await page.getByRole('tab', { name: 'Opencode v1', exact: true }).click()
  await page.getByRole('tab', { name: 'Opencode v2', exact: true }).click()
  await expect(buffer).toHaveValue('15%')
  await expect(permission).toHaveText('跳过确认')
  await permission.click()
  await page.getByRole('option', { name: '不设置', exact: true }).click()
  await expect.poll(() => preview()?.values?.permission).toBeNull()
  await page.getByRole('button', { name: '操作确认策略说明', exact: true }).hover()
  await expect(page.locator('.ui-help-content')).toContainText('规则数组')
  expect(fixture.unexpected).toEqual([])
})

test('Opencode V2 新配置使用 V2 协议包与共享模型控件', async ({ page }) => {
  const fixture = await mockAdmin(page)
  addExtensionFixtures(fixture)
  await page.goto('/agents')
  await page.getByRole('tab', { name: 'Opencode v2', exact: true }).click()
  await expect(
    page.getByText('/home/test/.config/opencode/opencode.json', { exact: true }),
  ).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'API 协议', exact: true })).toHaveText(
    'Chat Completions',
  )
  await expect(page.getByRole('combobox', { name: '默认主模型', exact: true })).toBeVisible()
  for (const label of ['操作确认策略', '自动压缩', '文件快照']) {
    await expect(page.getByRole('combobox', { name: label, exact: true })).toHaveText('不设置')
  }
  await expect(page.getByRole('combobox', { name: '更新检查', exact: true })).toHaveText('关闭')
  await expect(page.getByRole('combobox', { name: '会话分享', exact: true })).toHaveText('禁用分享')
  const context = page.getByRole('textbox', { name: '模型上下文', exact: true })
  await context.fill('131072')
  const preview = () =>
    (
      fixture.calls.filter((call) => call.path.endsWith('/opencode_v2/preview')).at(-1)
        ?.body as PreviewInput
    )?.draft
  expect(preview()?.values?.context).toBeUndefined()
  await context.press('Tab')
  await expect.poll(() => preview()?.values?.context).toBe(131072)
  await expect(page.getByRole('button', { name: 'Opencode v2 说明', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '设置 Agent 页签', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Agent 页签', exact: true })
  for (const version of [1, 2]) {
    const row = dialog.getByRole('listitem').filter({ hasText: `Opencode v${version}` })
    await row.getByRole('button', { name: `Opencode v${version} 说明`, exact: true }).hover()
    await expect(page.locator('.ui-help-content')).toContainText(`${version}.x`)
    await expect(page.locator('.ui-help-content')).toContainText('opencode --version')
    await expect(page.locator('.ui-help-content')).toContainText('同一配置路径')
    await row.getByRole('switch').hover()
    await expect(page.locator('.ui-help-content')).toHaveCount(0)
  }
})
