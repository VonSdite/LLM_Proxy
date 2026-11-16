import { expect, test } from '../browser-test'
import { mockProviderTable } from '../provider-fixtures'

test('认证条目多选原位切换，批量启停和删除仅修改草稿', async ({ page }) => {
  const fixture = await mockProviderTable(page)
  await page.goto('/')
  await page.getByRole('tab', { name: /Auth Groups/ }).click()
  await page.getByRole('button', { name: '编辑 Auth Group team-pool', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '编辑 Auth Group', exact: true })
  const sidebar = dialog.getByRole('navigation', { name: '认证条目' })
  await sidebar.getByRole('button', { name: '添加条目', exact: true }).click()
  const id = dialog.getByRole('textbox', { name: 'Entry ID', exact: true })
  await id.fill('key-b')
  const checkbox = sidebar.getByRole('checkbox', { name: '选择条目 key-a', exact: true })
  const before = (await checkbox.boundingBox())!
  await checkbox.click()
  await expect(id).toHaveValue('key-b')
  expect((await checkbox.boundingBox())!.y).toBe(before.y)
  const bar = dialog.getByRole('region', { name: '批量操作' })
  await expect(bar).toContainText('已选 1 项')
  await expect(dialog.getByRole('checkbox', { name: '选择全部认证条目' })).toBeVisible()
  await bar.getByRole('button', { name: '清空选择' }).click()
  await expect(checkbox).toBeFocused()
  await dialog.getByRole('checkbox', { name: '选择全部认证条目' }).click()
  await expect(bar).toContainText('已选 2 项')
  await expect(dialog.getByRole('checkbox', { name: '选择全部认证条目' })).toBeChecked()
  await expect(bar.getByRole('button', { name: '启用', exact: true })).toHaveCount(0)
  await bar.getByRole('button', { name: '禁用', exact: true }).click()
  await expect(sidebar.getByRole('switch', { checked: true })).toHaveCount(0)
  await expect(bar.getByRole('button', { name: '禁用', exact: true })).toHaveCount(0)
  await sidebar.getByRole('switch', { name: '启用条目 key-a', exact: true }).click()
  await expect(bar.getByRole('button', { name: '禁用', exact: true })).toBeVisible()
  await bar.getByRole('button', { name: '启用', exact: true }).click()
  await expect(sidebar.getByRole('switch', { checked: true })).toHaveCount(2)
  await bar.getByRole('button', { name: '删除', exact: true }).click()
  await page.locator('.ui-confirm').getByRole('button', { name: '确认', exact: true }).click()
  await expect(sidebar.getByRole('checkbox')).toHaveCount(0)
  await expect(bar).toHaveCount(0)
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('至少添加一个认证条目')
  expect(fixture.calls.filter((call) => call.method !== 'GET')).toEqual([])
})

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
  await page.getByRole('button', { name: '确认', exact: true }).click()
  await expect(sidebar.getByRole('button', { name: 'key-b', exact: true })).toHaveCount(0)
  await expect(id).toHaveValue('key-c')
  await sidebar.getByRole('button', { name: '移除条目 key-c', exact: true }).click()
  await page.getByRole('button', { name: '确认', exact: true }).click()
  await expect(id).toHaveValue('key-a')
  await sidebar.getByRole('button', { name: '移除条目 key-a', exact: true }).click()
  await page.getByRole('button', { name: '确认', exact: true }).click()
  await expect(id).toHaveCount(0)
  await sidebar.getByRole('button', { name: '添加条目', exact: true }).click()
  await expect(id).toHaveValue('')
  expect(fixture.calls.filter((call) => call.method !== 'GET')).toEqual([])
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  await expect(dialog).toHaveCount(0)
})

test('认证条目仅支持文本编辑，校验失败保留草稿，确定后保存认证组才写入', async ({ page }) => {
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
  const trigger = parent.getByRole('button', { name: '编辑', exact: true })
  await trigger.click()
  const editor = page.getByRole('dialog', { name: '编辑认证条目', exact: true })
  await expect(editor.locator('.ace_content')).toContainText('key-a')
  await page.keyboard.press('Escape')
  await expect(editor).toHaveCount(0)
  await expect(trigger).toBeFocused()
  await trigger.click()
  await expect(editor.locator('input[type=file]')).toHaveCount(0)
  await expect(editor.getByRole('button', { name: /选择.*文件/ })).toHaveCount(0)
  await editor.locator('.ace_text-input').focus()
  await page.keyboard.press('ControlOrMeta+A')
  await page.keyboard.insertText(
    'entries:\n  - id: imported-key\n    enabled: false\n    headers:\n      Authorization: Bearer fixture\n',
  )
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
