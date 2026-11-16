import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

test('映射多选测试逐行执行，仅锁定当前目标', async ({ page }) => {
  await mockAdmin(page)
  const requests: string[] = []
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route('**/api/providers/test-models', async (route) => {
    const model = route.request().postDataJSON().models[0] as string
    requests.push(model)
    if (requests.length === 1) await pending
    await route.fulfill({
      json: {
        results: [
          {
            requested_model: model,
            available: true,
            first_token_latency_ms: 28,
            tps: 52,
            response_model: model,
            error: null,
          },
        ],
      },
    })
  })
  await page.goto('/model-mappings')
  await page.getByRole('button', { name: '编辑模型映射 smart', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '编辑模型映射', exact: true })
  await dialog.getByRole('button', { name: '编辑', exact: true }).click()
  const editor = page.getByRole('dialog', { name: '编辑目标模型', exact: true })
  await editor.locator('.ace_text-input').focus()
  await page.keyboard.press('ControlOrMeta+A')
  await page.keyboard.insertText(
    'main/claude-sonnet-4-6\nmain/org/very-long-model-name-with-full-routing-path-20261106',
  )
  await editor.getByRole('button', { name: '确定', exact: true }).click()
  await dialog.getByRole('checkbox', { name: '选择当前列表', exact: true }).click()
  const batch = dialog
    .getByRole('region', { name: '批量操作' })
    .getByRole('button', { name: '测试', exact: true })
  await batch.click()
  await expect.poll(() => requests.length).toBe(1)
  await expect(dialog.getByRole('combobox', { name: '目标 1', exact: true })).toBeDisabled()
  await expect(dialog.getByRole('combobox', { name: '目标 2', exact: true })).toBeEnabled()
  await expect(dialog.getByRole('button', { name: '停止测试', exact: true })).toBeVisible()
  release()
  await expect
    .poll(() => requests)
    .toEqual(['claude-sonnet-4-6', 'org/very-long-model-name-with-full-routing-path-20261106'])
  await expect(batch).toBeEnabled()
  await expect(dialog.getByRole('button', { name: '停止测试', exact: true })).toHaveCount(0)
  await expect(dialog.getByRole('columnheader', { name: '首字延迟' })).toBeVisible()
  await expect(dialog.getByRole('columnheader', { name: 'TPS' })).toBeVisible()
  await expect(dialog.getByText('28 ms', { exact: true })).toHaveCount(2)
  await expect(dialog.getByText('52 tok/s', { exact: true })).toHaveCount(2)
  await expect(dialog.getByText('可用', { exact: true })).toHaveCount(2)
  await page.route('**/api/providers/test-models', async (route) => {
    await route.fulfill({
      json: {
        results: [
          {
            requested_model: 'model',
            available: false,
            first_token_latency_ms: null,
            tps: null,
            response_model: null,
            error: 'Upstream authentication failed',
          },
        ],
      },
    })
  })
  await batch.click()
  await expect(dialog.getByText('失败', { exact: true })).toHaveCount(2)
  await expect(dialog.getByText('Upstream authentication failed', { exact: true })).toHaveCount(2)
  await expect(dialog.getByText('28 ms', { exact: true })).toHaveCount(0)
  await expect(dialog.getByText('52 tok/s', { exact: true })).toHaveCount(0)
})
