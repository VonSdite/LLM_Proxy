import type { Page } from '@playwright/test'
import type { PreviewInput } from '../../src/api/agents'
import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'
import { addExtensionFixtures } from '../agent-extension-fixtures'

async function setup(page: Page, agent = 'claude', label = 'Claude', target = 'claude') {
  const fixture = await mockAdmin(page)
  addExtensionFixtures(fixture)
  const file = fixture.snapshot.files[target]!
  file.version = 'initial'
  const previews: PreviewInput[] = []
  const writes: unknown[] = []
  let rejectWrite = false
  let validationError = false
  await page.route(`**/api/agent-config/machines/local/${agent}/preview`, async (route) => {
    const body = route.request().postDataJSON() as PreviewInput
    previews.push(body)
    if (validationError) {
      await route.fulfill({ status: 400, json: { error: '模型配置无效', code: 'invalid_config' } })
    } else if (body.versions[target] !== file.version) {
      await route.fulfill({
        status: 409,
        json: { error: '配置文件已被其他程序修改，请刷新后重新预览', code: 'file_conflict' },
      })
    } else {
      await route.fulfill({
        json: {
          token: `ticket-${file.version}`,
          hasChanges: true,
          rows: [
            {
              file: target,
              path: 'api.address',
              kind: 'change',
              current: 'external-value',
              next: 'draft-value',
            },
          ],
        },
      })
    }
  })
  await page.route('**/api/agent-config/machines/local/apply', async (route) => {
    writes.push(route.request().postDataJSON())
    if (rejectWrite) {
      file.version = 'changed-again'
      await route.fulfill({
        status: 409,
        json: { error: '配置文件再次变化', code: 'file_conflict' },
      })
    } else await route.fulfill({ json: fixture.snapshot })
  })
  await page.goto('/agents')
  if (agent !== 'claude') await page.getByRole('tab', { name: label, exact: true }).click()
  const panel = page.locator('.agent-preview')
  await expect(panel.getByRole('status')).toHaveText('有待保存的修改')
  const address = page.getByRole('combobox', { name: 'API 地址', exact: true })
  const original = await address.inputValue()
  return {
    file,
    previews,
    writes,
    address,
    original,
    panel,
    save: panel.getByRole('button', { name: '保存', exact: true }),
    dialog: page.getByRole('dialog', { name: '覆盖外部修改？' }),
    rejectWrite: () => {
      rejectWrite = true
    },
    invalidate: () => {
      validationError = true
    },
    async edit(conflict = true) {
      if (conflict) file.version = 'external'
      await address.fill('https://draft.example.com/v1')
      await address.press('Tab')
      await expect(panel.getByRole('status')).toContainText(
        conflict ? '配置文件已被其他程序修改' : '有待保存的修改',
      )
    },
  }
}

for (const [agent, label, target] of [
  ['claude', 'Claude', 'claude'],
  ['codex', 'Codex', 'codex'],
  ['opencode', 'Opencode v1', 'opencode'],
  ['opencode_v2', 'Opencode v2', 'opencodeV2'],
  ['hermes', 'Hermes', 'hermes'],
]) {
  test(`${label} 外部冲突仍可保存，取消保留草稿，确认使用最新预览`, async ({ page }) => {
    const f = await setup(page, agent, label, target)
    await f.edit()
    await expect(f.save).toBeEnabled()
    await expect(f.panel.getByRole('button', { name: '刷新', exact: true })).toBeVisible()
    await f.save.click()
    await expect(f.dialog).toContainText('external-value → draft-value')
    expect(f.previews.at(-1)?.versions[target!]).toBe('external')
    expect(f.writes).toEqual([])
    await f.dialog.getByRole('button', { name: '取消', exact: true }).click()
    await expect(f.address).toHaveValue('https://draft.example.com/v1')
    expect(f.writes).toEqual([])
    await f.save.click()
    await f.dialog.getByRole('button', { name: '确认覆盖', exact: true }).click()
    await expect(f.dialog).toBeHidden()
    await expect(f.panel.getByRole('status')).toHaveText('无修改')
    expect(f.writes).toEqual([{ token: 'ticket-external' }])
  })
}

test('红字旁刷新加载最新文件，确认前保留未保存草稿', async ({ page }, testInfo) => {
  const f = await setup(page)
  await f.edit()
  f.file.managed['env.ANTHROPIC_BASE_URL'] = 'https://latest.example.com'
  await f.panel.screenshot({ path: testInfo.outputPath('conflict-refresh.png') })
  await f.panel.getByRole('button', { name: '刷新', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '重新读取配置？' })
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  await expect(f.address).toHaveValue('https://draft.example.com/v1')
  await f.panel.getByRole('button', { name: '刷新', exact: true }).click()
  await dialog.getByRole('button', { name: '重新读取', exact: true }).click()
  await expect(f.address).toHaveValue('https://latest.example.com')
  await expect(f.panel.getByRole('button', { name: '刷新', exact: true })).toBeHidden()
  expect(f.writes).toEqual([])
})

test('确认覆盖期间再次变化会拒绝写入并保留草稿', async ({ page }) => {
  const f = await setup(page)
  await f.edit()
  await f.save.click()
  await expect(f.dialog).toBeVisible()
  f.rejectWrite()
  await f.dialog.getByRole('button', { name: '确认覆盖', exact: true }).click()
  await expect(f.dialog).toBeHidden()
  await expect(f.address).toHaveValue('https://draft.example.com/v1')
  await expect(f.panel.getByRole('status')).toContainText('配置文件已被其他程序修改')
  await expect(f.save).toBeEnabled()
  expect(f.writes).toEqual([{ token: 'ticket-external' }])
})

test('保存时才发现冲突也会先生成覆盖确认', async ({ page }) => {
  const f = await setup(page)
  await f.edit(false)
  f.file.version = 'external'
  await f.save.click()
  await expect(f.dialog).toBeVisible()
  expect(f.writes).toEqual([])
})

test('字段校验错误不会启用覆盖保存', async ({ page }) => {
  const f = await setup(page)
  f.invalidate()
  await f.address.fill('https://draft.example.com/v1')
  await f.address.press('Tab')
  await expect(f.panel.getByRole('status')).toHaveText('模型配置无效')
  await expect(f.save).toBeDisabled()
  expect(f.writes).toEqual([])
})
