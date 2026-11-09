import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

const promptTitle = '保存修改？'
const editors = [
  { path: '/', trigger: '编辑 Provider main', title: '编辑 Provider', field: 'Provider 名称' },
  { path: '/', trigger: '新增 Auth Group', title: '新增 Auth Group', field: '名称', group: true },
  { path: '/model-mappings', trigger: '创建映射', title: '创建模型映射', field: '对外模型 ID' },
  { path: '/api-keys', trigger: '创建 API Key', title: '创建 API Key', field: '名称' },
  { path: '/users', trigger: '添加用户', title: '添加用户', field: '用户名' },
  { path: '/agents', trigger: '添加 SSH 机器', title: '添加 SSH 机器', field: 'IP / 主机名' },
  {
    path: '/agents',
    trigger: 'settings.json 路径设置',
    title: 'Claude 配置文件',
    field: '文件路径',
  },
  { path: '/oauth', trigger: '添加账号', title: '添加 Codex 账号', field: '回调 URL' },
]

for (const editor of editors) {
  test(`${editor.title}：外侧和 Esc 保护草稿，关闭与取消直接放弃`, async ({ page }) => {
    const fixture = await mockAdmin(page)
    await page.goto(editor.path)
    if ('group' in editor) await page.getByRole('tab', { name: /Auth Groups/ }).click()
    const trigger = page.getByRole('button', { name: editor.trigger, exact: true })
    await trigger.click()
    const dialog = page.getByRole('dialog', { name: editor.title, exact: true })
    await dialog.getByRole('button', { name: '取消', exact: true }).click()
    await expect(dialog).toHaveCount(0)
    await trigger.click()
    const input = dialog.getByRole('textbox', { name: editor.field, exact: true })
    const original = await input.inputValue()
    await input.fill('unsaved-draft')
    await page.mouse.click(4, 80)
    const prompt = page.getByRole('dialog', { name: promptTitle, exact: true })
    await expect(prompt).toBeVisible()
    await expect(prompt.getByRole('button', { name: '关闭', exact: true })).not.toBeFocused()
    await expect(prompt.getByRole('button', { name: '继续编辑', exact: true })).toBeFocused()
    await prompt.getByRole('button', { name: '继续编辑', exact: true }).click()
    await expect(input).toHaveValue('unsaved-draft')
    await page.keyboard.press('Escape')
    await expect(prompt).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(prompt).toHaveCount(0)
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: '关闭', exact: true }).click()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(trigger).toBeFocused()
    await trigger.click()
    await expect(input).toHaveValue(original)
    await input.fill('changed')
    await dialog.getByRole('button', { name: '取消', exact: true }).click()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(trigger).toBeFocused()
    await trigger.click()
    await expect(input).toHaveValue(original)
    await input.fill('changed')
    await input.fill(original)
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0)
    expect(
      fixture.calls.filter((call) => call.method !== 'GET' && !call.path.endsWith('/preview')),
    ).toEqual([])
    expect(fixture.unexpected).toEqual([])
  })
}

test('鼠标关闭 Provider 后焦点返回但不冒出气泡，真实悬停与键盘聚焦仍可查看提示', async ({
  page,
}) => {
  await mockAdmin(page)
  await page.goto('/')
  const trigger = page.getByRole('button', { name: '编辑 Provider main', exact: true })
  await trigger.click()
  await page.mouse.click(4, 80)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(trigger).toBeFocused()
  // 等待超过提示组件的延时，确认没有恢复焦点引发的迟发气泡。
  await page.waitForTimeout(300)
  await expect(page.locator('.ui-tooltip')).toHaveCount(0)
  await trigger.hover()
  await expect(page.locator('.ui-tooltip')).toBeVisible()
  await page.mouse.move(4, 80)
  await trigger.press('Tab')
  await page.keyboard.press('Shift+Tab')
  await expect(trigger).toBeFocused()
  await expect(page.locator('.ui-tooltip')).toBeVisible()
})

test('尚未失焦和无效数值、空模型行都受保护；显示密钥不计入修改', async ({ page }) => {
  await mockAdmin(page)
  await page.goto('/')
  const trigger = page.getByRole('button', { name: '编辑 Provider main', exact: true })
  const dialog = page.getByRole('dialog', { name: '编辑 Provider', exact: true })
  const prompt = page.getByRole('dialog', { name: promptTitle, exact: true })
  for (const value of ['7', 'invalid']) {
    await trigger.click()
    await dialog.getByRole('textbox', { name: '最大尝试次数', exact: true }).fill(value)
    await page.keyboard.press('Escape')
    await expect(prompt).toBeVisible()
    await prompt.getByRole('button', { name: '继续编辑', exact: true }).click()
    await expect(dialog.getByRole('textbox', { name: '最大尝试次数', exact: true })).toHaveValue(
      value,
    )
    await dialog.getByRole('button', { name: '取消', exact: true }).click()
    await expect(prompt).toHaveCount(0)
    await expect(dialog).toHaveCount(0)
  }
  await trigger.click()
  await dialog.getByRole('button', { name: '添加', exact: true }).click()
  await page.keyboard.press('Escape')
  await expect(prompt).toBeVisible()
  await prompt.getByRole('button', { name: '不保存', exact: true }).click()
  await trigger.click()
  await dialog.getByRole('button', { name: '显示内容', exact: true }).click()
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('认证条目限速恢复留空后不再提示未保存', async ({ page }) => {
  await mockAdmin(page)
  await page.goto('/')
  await page.getByRole('tab', { name: /Auth Groups/ }).click()
  await page.getByRole('button', { name: '新增 Auth Group', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '新增 Auth Group', exact: true })
  const input = dialog.getByRole('textbox', { name: '最大并发', exact: true })
  await input.fill('2')
  await input.press('Tab')
  await input.clear()
  await input.press('Tab')
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('保存失败保留关闭保护，成功保存直接关闭且不再提示', async ({ page }) => {
  const fixture = await mockAdmin(page)
  let attempts = 0
  await page.route('**/api/providers/main', async (route) => {
    attempts++
    await route.fulfill(
      attempts === 1
        ? { status: 400, json: { error: 'Save failed' } }
        : { json: { message: 'ok' } },
    )
  })
  await page.goto('/')
  await page.getByRole('button', { name: '编辑 Provider main', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '编辑 Provider', exact: true })
  await dialog
    .getByRole('textbox', { name: 'API 地址', exact: true })
    .fill('https://changed.test/v1/chat/completions')
  const prompt = page.getByRole('dialog', { name: promptTitle, exact: true })
  await page.keyboard.press('Escape')
  await prompt.getByRole('button', { name: '保存并关闭', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('Save failed')
  await page.mouse.click(4, 80)
  await prompt.getByRole('button', { name: '保存并关闭', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(attempts).toBe(2)
  expect(fixture.unexpected).toEqual([])
})

test('数值恢复原值后直接关闭，包括尚未失焦和等价数字格式', async ({ page }) => {
  await mockAdmin(page)
  await page.goto('/')
  for (const action of ['escape', 'outside', 'cancel']) {
    await page.getByRole('button', { name: '编辑 Provider main', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: '编辑 Provider', exact: true })
    const input = dialog.getByRole('textbox', { name: '最大尝试次数', exact: true })
    const original = await input.inputValue()
    await input.fill('9')
    await input.press('Tab')
    await input.fill(`${original}.0`)
    if (action === 'escape') await input.press('Escape')
    else if (action === 'outside') await page.mouse.click(4, 80)
    else await dialog.getByRole('button', { name: '取消', exact: true }).click()
    await expect(page.getByRole('dialog')).toHaveCount(0)
  }
})

test('保存并关闭复用表单校验，未通过时保留草稿且不发请求', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/')
  await page.getByRole('button', { name: '编辑 Provider main', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '编辑 Provider', exact: true })
  const input = dialog.getByRole('textbox', { name: 'Provider 名称', exact: true })
  await input.fill('invalid-name')
  await page.mouse.click(4, 80)
  const prompt = page.getByRole('dialog', { name: promptTitle, exact: true })
  await prompt.getByRole('button', { name: '保存并关闭', exact: true }).click()
  await expect(prompt).toHaveCount(0)
  await expect(input).toHaveValue('invalid-name')
  await expect(input).toHaveAttribute('aria-invalid', 'true')
  expect(fixture.calls.filter((call) => call.method !== 'GET')).toEqual([])
})

test('嵌套模型选择只丢弃当前选择，不关闭 Provider 或丢失外层草稿', async ({ page }) => {
  await mockAdmin(page)
  await page.route('**/api/providers/fetch-models?*', (route) =>
    route.fulfill({ json: { fetched_models: ['new-model'] } }),
  )
  await page.goto('/')
  await page.getByRole('button', { name: '编辑 Provider main', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '编辑 Provider', exact: true })
  await dialog.getByRole('textbox', { name: 'Provider 名称', exact: true }).fill('changed')
  await dialog.getByRole('button', { name: '拉取模型', exact: true }).click()
  const picker = page.getByRole('dialog', { name: '选择上游模型', exact: true })
  await picker.getByRole('checkbox', { name: 'new-model', exact: true }).click()
  await page.keyboard.press('Escape')
  const prompt = page.getByRole('dialog', { name: promptTitle, exact: true })
  await prompt.getByRole('button', { name: '不保存', exact: true }).click()
  await expect(picker).toHaveCount(0)
  await expect(dialog.getByRole('textbox', { name: 'Provider 名称', exact: true })).toHaveValue(
    'changed',
  )
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('导入草稿与批量权限也有关闭保护', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/users')
  await page.getByRole('button', { name: '导入', exact: true }).click()
  await page.locator('input[type=file]').setInputFiles({
    name: 'users.json',
    mimeType: 'application/json',
    buffer: Buffer.from('{"users":[]}'),
  })
  const dialog = page.getByRole('dialog').first()
  await expect(dialog.locator('.ace_content')).toContainText('users')
  await page.keyboard.press('Escape')
  const prompt = page.getByRole('dialog', { name: promptTitle, exact: true })
  await prompt.getByRole('button', { name: '不保存', exact: true }).click()
  await page.getByRole('checkbox', { name: '选择 1', exact: true }).click()
  await page
    .getByRole('region', { name: '批量操作' })
    .getByRole('button', { name: '模型权限', exact: true })
    .click()
  await page.getByRole('dialog').getByRole('tab', { name: '指定模型', exact: true }).click()
  await page.mouse.click(4, 80)
  await expect(prompt).toBeVisible()
  expect(fixture.calls.filter((call) => call.method !== 'GET')).toEqual([])
})
