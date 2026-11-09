import { expect, test } from '../browser-test'
import { mockProviderTable } from '../provider-fixtures'

test('侧栏启停、添加和移除只更新草稿，删除当前条目后选择相邻条目', async ({ page }) => {
  const fixture = await mockProviderTable(page)
  await page.goto('/')
  await page.getByRole('tab', { name: /Auth Groups/ }).click()
  await page.getByRole('button', { name: '编辑 Auth Group team-pool', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '编辑 Auth Group', exact: true })
  const sidebar = dialog.getByRole('navigation', { name: '认证条目' })
  await sidebar.getByRole('switch', { name: '启用条目 key-a', exact: true }).click()
  await expect(
    sidebar.getByRole('switch', { name: '启用条目 key-a', exact: true }),
  ).not.toBeChecked()
  await sidebar.getByRole('button', { name: '添加条目', exact: true }).click()
  const id = dialog.getByRole('textbox', { name: 'Entry ID', exact: true })
  await expect(id).toHaveValue('')
  await id.fill('key-b')
  await expect(sidebar.getByRole('button', { name: 'key-b', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  )
  await sidebar.getByRole('switch', { name: '启用条目 key-a', exact: true }).click()
  await expect(id).toHaveValue('key-b')
  await sidebar.getByRole('button', { name: '添加条目', exact: true }).click()
  await id.fill('key-c')
  await sidebar.getByRole('button', { name: '移除条目 key-b', exact: true }).click()
  await page.getByRole('button', { name: '确认删除', exact: true }).click()
  await expect(sidebar.getByRole('button', { name: 'key-b', exact: true })).toHaveCount(0)
  await expect(id).toHaveValue('key-c')
  await sidebar.getByRole('button', { name: '移除条目 key-c', exact: true }).click()
  await page.getByRole('button', { name: '确认删除', exact: true }).click()
  await expect(id).toHaveValue('key-a')
  await sidebar.getByRole('button', { name: '移除条目 key-a', exact: true }).click()
  await page.getByRole('button', { name: '确认删除', exact: true }).click()
  await expect(id).toHaveCount(0)
  await sidebar.getByRole('button', { name: '添加条目', exact: true }).click()
  await expect(id).toHaveValue('')
  expect(fixture.calls.filter((call) => call.method !== 'GET')).toEqual([])
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  await expect(dialog).toHaveCount(0)
})

test('编辑和文件导入共用窗口，校验失败保留草稿，确定后保存认证组才写入', async ({ page }) => {
  await mockProviderTable(page)
  const replacement = {
    id: 'imported-key',
    enabled: false,
    headers: { Authorization: 'Bearer fixture' },
  }
  let saves = 0
  let parses = 0
  await page.route('**/api/auth-groups/team-pool', async (route) => {
    saves++
    expect(route.request().postDataJSON().entries).toEqual([expect.objectContaining(replacement)])
    await route.fulfill({ json: route.request().postDataJSON() })
  })
  await page.route('**/api/auth-groups/import-entries', async (route) => {
    parses++
    const { yaml } = route.request().postDataJSON() as { yaml: string }
    expect(yaml).toContain('imported-key')
    await route.fulfill(
      parses === 1
        ? { status: 400, json: { error: 'YAML 校验失败' } }
        : { json: { entries: [replacement] } },
    )
  })
  await page.goto('/')
  await page.getByRole('tab', { name: /Auth Groups/ }).click()
  await page.getByRole('button', { name: '编辑 Auth Group team-pool', exact: true }).click()
  const parent = page.getByRole('dialog', { name: '编辑 Auth Group', exact: true })
  const trigger = parent.getByRole('button', { name: '编辑 / 导入', exact: true })
  await trigger.click()
  const editor = page.getByRole('dialog', { name: '编辑认证条目', exact: true })
  await expect(editor.locator('.ace_content')).toContainText('key-a')
  await page.keyboard.press('Escape')
  await expect(editor).toHaveCount(0)
  await expect(trigger).toBeFocused()
  await trigger.click()
  await editor.locator('input[type=file]').setInputFiles({
    name: 'entries.yaml',
    mimeType: 'text/yaml',
    buffer: Buffer.from(
      'entries:\n  - id: imported-key\n    enabled: false\n    headers:\n      Authorization: Bearer fixture\n',
    ),
  })
  await expect(editor.locator('.ace_content')).toContainText('imported-key')
  await page.keyboard.press('Escape')
  const prompt = page.getByRole('dialog', { name: '保存修改？', exact: true })
  await expect(prompt).toBeVisible()
  await prompt.getByRole('button', { name: '继续编辑', exact: true }).click()
  await editor.getByRole('button', { name: '确定', exact: true }).click()
  await expect(editor.getByRole('alert')).toContainText('YAML 校验失败')
  expect(saves).toBe(0)
  await editor.getByRole('button', { name: '确定', exact: true }).click()
  await expect(editor).toHaveCount(0)
  await expect(parent.getByRole('textbox', { name: 'Entry ID', exact: true })).toHaveValue(
    'imported-key',
  )
  await expect(
    parent.getByRole('switch', { name: '启用条目 imported-key', exact: true }),
  ).not.toBeChecked()
  expect(saves).toBe(0)
  await parent.getByRole('button', { name: '保存', exact: true }).click()
  await expect(parent).toHaveCount(0)
  expect(saves).toBe(1)
})
