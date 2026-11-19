import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'
import { addExtensionFixtures } from '../agent-extension-fixtures'
import type { PreviewInput } from '@/api/agents'

test('DSH 与 Hermes 从目录定义生成表单、数值失焦提交、布尔值和草稿保留', async ({
  page,
}, testInfo) => {
  const fixture = await mockAdmin(page)
  addExtensionFixtures(fixture)
  await page.goto('/agents')
  await page.getByRole('tab', { name: 'DSH', exact: true }).click()
  await expect(page.getByText('cordis.patch.yml', { exact: false }).first()).toBeVisible()
  await expect(page.getByText('/home/test/.dsh/cordis.patch.yml', { exact: true })).toBeVisible()
  const draft = (agent: string) =>
    (
      fixture.calls.filter((call) => call.path.endsWith(`/${agent}/preview`)).at(-1)
        ?.body as PreviewInput
    )?.draft
  const context = page.getByRole('textbox', { name: '模型上下文', exact: true })
  const permission = page.getByRole('combobox', { name: '默认权限模式', exact: true })
  await expect(permission).toHaveText('不设置')
  await permission.click()
  await page.getByRole('option', { name: '完全权限', exact: true }).click()
  await expect.poll(() => draft('dsh')?.values?.permission).toBe('danger-full-access')
  await page.getByRole('button', { name: '默认权限模式说明', exact: true }).hover()
  await expect(page.locator('.ui-help-content')).toContainText('never 不是自动批准')
  await context.click()
  await context.fill('131072')
  await expect.poll(() => draft('dsh')?.values?.context).toBeUndefined()
  await context.press('Tab')
  await expect.poll(() => draft('dsh')?.values?.context).toBe(131072)
  const effort = page.getByRole('combobox', { name: '思考强度', exact: true })
  await effort.click()
  await page.screenshot({ path: testInfo.outputPath('dsh-effort-options.png') })
  await page.getByRole('option', { name: '高（high）', exact: true }).click()
  await expect(effort).toHaveText('高（high）')
  await expect.poll(() => draft('dsh')?.values?.effort).toBe('high')
  for (const [value, label] of [
    ['minimal', '极低（minimal）'],
    ['xhigh', '极高（xhigh）'],
    ['max', '最高（max）'],
  ]) {
    await effort.click()
    await page.getByRole('option', { name: label, exact: true }).click()
    await expect.poll(() => draft('dsh')?.values?.effort).toBe(value)
  }
  await effort.click()
  await page.getByRole('option', { name: '不设置', exact: true }).click()
  await expect(effort).toHaveText('不设置')
  await expect.poll(() => draft('dsh')?.values?.effort).toBeNull()
  await page.getByRole('tab', { name: 'Hermes', exact: true }).click()
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
  await page.getByRole('tab', { name: 'DSH', exact: true }).click()
  await expect(context).toHaveValue('131072')
  await expect(permission).toHaveText('完全权限')
  await permission.click()
  await page.getByRole('option', { name: '不设置', exact: true }).click()
  await expect.poll(() => draft('dsh')?.values?.permission).toBeNull()
  await page.getByRole('combobox', { name: '默认主模型', exact: true }).click()
  await page.getByRole('option', { name: '不设置', exact: true }).click()
  await expect(effort).toBeDisabled()
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
  await page.getByRole('tab', { name: 'DSH', exact: true }).click()
  await page.getByRole('tab', { name: 'Hermes', exact: true }).click()
  await expect(provider).toHaveValue('custom:work')
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
  await page.getByRole('switch', { name: '显示 DSH', exact: true }).click()
  const after = await popup.boundingBox()
  expect(Math.abs(before!.x - after!.x)).toBeLessThan(1)
  await popup.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByRole('tab', { name: 'DSH', exact: true })).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('tab', { name: 'Sample', exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'DSH', exact: true })).toHaveCount(0)
})
