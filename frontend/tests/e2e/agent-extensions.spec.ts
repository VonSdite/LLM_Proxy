import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'
import { addExtensionFixtures } from '../agent-extension-fixtures'
import type { PreviewInput } from '@/api/agents'

test('Hermes 从目录定义生成表单、数值失焦提交、布尔值和草稿保留', async ({ page }) => {
  const fixture = await mockAdmin(page)
  addExtensionFixtures(fixture)
  await page.goto('/agents')
  await page.getByRole('tab', { name: 'Hermes', exact: true }).click()
  await expect(page.getByText('/home/test/.hermes/config.yaml', { exact: true })).toBeVisible()
  const draft = (agent: string) =>
    (
      fixture.calls.filter((call) => call.path.endsWith(`/${agent}/preview`)).at(-1)
        ?.body as PreviewInput
    )?.draft
  const context = page.getByRole('textbox', { name: '模型上下文', exact: true })
  await context.click()
  await context.fill('131072')
  await expect.poll(() => draft('hermes')?.values?.context).toBeUndefined()
  await context.press('Tab')
  await expect.poll(() => draft('hermes')?.values?.context).toBe(131072)
  const effort = page.getByRole('combobox', { name: '思考强度', exact: true })
  await effort.click()
  await page.getByRole('option', { name: 'high', exact: true }).click()
  await expect(effort).toHaveText('high')
  await expect.poll(() => draft('hermes')?.values?.effort).toBe('high')
  for (const value of ['minimal', 'xhigh', 'max']) {
    await effort.click()
    await page.getByRole('option', { name: value, exact: true }).click()
    await expect.poll(() => draft('hermes')?.values?.effort).toBe(value)
  }
  await effort.click()
  await page.getByRole('option', { name: '不设置', exact: true }).click()
  await expect(effort).toHaveText('不设置')
  await expect.poll(() => draft('hermes')?.values?.effort).toBeNull()
  const memory = page.getByRole('combobox', { name: '长期记忆', exact: true })
  await expect(memory).toHaveText('启用')
  await expect(page.getByRole('combobox', { name: '用户画像', exact: true })).toHaveText('启用')
  await memory.click()
  await page.getByRole('option', { name: '禁用', exact: true }).click()
  await expect.poll(() => draft('hermes')?.values?.memory).toBe(false)
  const approval = page.getByRole('combobox', { name: '命令确认', exact: true })
  await approval.click()
  await page.getByRole('option', { name: '跳过确认', exact: true }).click()
  await expect.poll(() => draft('hermes')?.values?.approvals).toBe('off')
  await page.getByRole('tab', { name: 'Opencode v2', exact: true }).click()
  await page.getByRole('tab', { name: 'Hermes', exact: true }).click()
  await expect(context).toHaveValue('131072')
  await expect(memory).toHaveText('禁用')
  await expect(approval).toHaveText('跳过确认')
  expect(fixture.unexpected).toEqual([])
})

test('Hermes 服务类型可搜索选择，已有命名服务和手填标识原样保留', async ({ page }) => {
  const fixture = await mockAdmin(page)
  addExtensionFixtures(fixture)
  Object.assign(fixture.snapshot.files.hermes!, {
    exists: true,
    managed: { provider: 'custom:proxy', model: 'main/a' },
  })
  const draft = () =>
    (
      fixture.calls.filter((call) => call.path.endsWith('/hermes/preview')).at(-1)
        ?.body as PreviewInput
    )?.draft
  await page.goto('/agents')
  await page.getByRole('tab', { name: 'Hermes', exact: true }).click()
  const provider = page.getByRole('combobox', { name: '服务类型', exact: true })
  await expect(provider).toHaveValue('custom:proxy')
  await expect.poll(() => draft()?.values?.provider).toBe('custom:proxy')
  await provider.click()
  await page.getByRole('option', { name: '自定义 API（本服务）', exact: true }).click()
  await expect(provider).toHaveValue('自定义 API（本服务）')
  await expect.poll(() => draft()?.values?.provider).toBe('custom')
  await provider.fill('openr')
  await expect(page.getByRole('option', { name: 'OpenRouter', exact: true })).toBeVisible()
  await provider.press('ArrowDown')
  await provider.press('Enter')
  await expect(provider).toHaveValue('OpenRouter')
  await expect.poll(() => draft()?.values?.provider).toBe('openrouter')
  await provider.fill('custom:work')
  await provider.press('Tab')
  await expect.poll(() => draft()?.values?.provider).toBe('custom:work')
  await page.getByRole('tab', { name: 'Opencode v2', exact: true }).click()
  await page.getByRole('tab', { name: 'Hermes', exact: true }).click()
  await expect(provider).toHaveValue('custom:work')
  expect(fixture.unexpected).toEqual([])
})

test('已移除的 Agent 页签偏好回退到可用项，设置弹窗仅显示已注册项', async ({ page }, testInfo) => {
  const fixture = await mockAdmin(page)
  addExtensionFixtures(fixture)
  await page.addInitScript(() => {
    if (localStorage.getItem('agent-tab-preferences')) return
    localStorage.setItem('agent-tab-preferences', JSON.stringify([{ id: 'dsh', visible: true }]))
    localStorage.setItem('agent-active-tab', 'dsh')
  })
  await page.goto('/agents')
  const tabs = page.getByRole('tablist', { name: 'Agent 类型' })
  await expect(tabs.getByRole('tab')).toHaveText([
    'Claude',
    'Codex',
    'Opencode v1',
    'Opencode v2',
    'Hermes',
  ])
  await expect(tabs.getByRole('tab', { name: 'Claude', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await page.getByRole('button', { name: '设置 Agent 页签', exact: true }).click()
  const popup = page.getByRole('dialog', { name: 'Agent 页签', exact: true })
  await expect(popup.getByRole('switch')).toHaveCount(5)
  await expect(popup.getByText('DSH', { exact: true })).toHaveCount(0)
  await popup.getByRole('button', { name: '取消', exact: true }).click()
  await page.reload()
  await expect(tabs.getByRole('tab', { name: 'Claude', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await page.screenshot({ path: testInfo.outputPath('agents.png') })
  expect(fixture.calls.some((call) => call.path.endsWith('/dsh/preview'))).toBe(false)
  expect(fixture.unexpected).toEqual([])
})

test('新增扩展自动显示且排序显隐跨刷新恢复，弹窗内切换不移动标签', async ({ page }) => {
  const fixture = await mockAdmin(page)
  addExtensionFixtures(fixture)
  fixture.agent.agents!.push({
    id: 'sample',
    label: 'Sample',
    files: ['sample'],
    fields: [
      { id: 'enabled', label: '测试开关', component: 'boolean', file: 'sample', path: ['enabled'] },
    ],
  })
  fixture.snapshot.files.sample = {
    ...fixture.snapshot.files.hermes!,
    path: '/home/test/.sample/settings.json',
    managed: { enabled: true },
  }
  await page.addInitScript(() => {
    if (localStorage.getItem('agent-tab-preferences')) return
    localStorage.setItem(
      'agent-tab-preferences',
      JSON.stringify([
        { id: 'sample', visible: true },
        { id: 'hermes', visible: false },
      ]),
    )
  })
  await page.goto('/agents')
  await expect(page.getByRole('tab', { name: 'Sample', exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Hermes', exact: true })).toHaveCount(0)
  await page.getByRole('tab', { name: 'Sample', exact: true }).click()
  await expect(page.getByRole('combobox', { name: '测试开关', exact: true })).toHaveText('启用')
  await page.getByRole('button', { name: '设置 Agent 页签', exact: true }).click()
  const popup = page.getByRole('dialog', { name: 'Agent 页签', exact: true })
  const before = await popup.boundingBox()
  await page.getByRole('switch', { name: '显示 Opencode v2', exact: true }).click()
  const after = await popup.boundingBox()
  expect(Math.abs(before!.x - after!.x)).toBeLessThan(1)
  await popup.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByRole('tab', { name: 'Opencode v2', exact: true })).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('tab', { name: 'Sample', exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Opencode v2', exact: true })).toHaveCount(0)
})
