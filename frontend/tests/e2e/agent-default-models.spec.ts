import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'
import { addExtensionFixtures } from '../agent-extension-fixtures'
import type { PreviewInput } from '@/api/agents'

for (const [agent, label, target] of [
  ['opencode', 'Opencode v1', 'opencode'],
  ['opencode_v2', 'Opencode v2', 'opencodeV2'],
  ['hermes', 'Hermes', 'hermes'],
] as const) {
  test(`${label} 默认模型可不设置，保存刷新后保持为空`, async ({ page }) => {
    const fixture = await mockAdmin(page)
    addExtensionFixtures(fixture)
    const file = fixture.snapshot.files[target]!
    const model = fixture.agent.models[0]!
    file.exists = true
    file.managed.model = agent === 'opencode' ? `proxy/${model}` : model
    await page.route('**/api/agent-config/machines/local/apply', async (route) => {
      delete file.managed.model
      await route.fulfill({ json: fixture.snapshot })
    })
    await page.goto('/agents')
    await page.getByRole('tab', { name: label, exact: true }).click()
    const selector = page.getByRole('combobox', {
      name: agent === 'opencode' ? '模型' : '默认主模型',
      exact: true,
    })
    await expect(selector).toHaveValue(model)
    await selector.click()
    await page.getByRole('option', { name: '不设置', exact: true }).click()
    await expect(selector).toHaveValue('不设置')
    const draft = () =>
      (
        fixture.calls.filter((call) => call.path.endsWith(`/${agent}/preview`)).at(-1)
          ?.body as PreviewInput
      )?.draft
    await expect
      .poll(() => (agent === 'opencode' ? draft()?.model : draft()?.values?.model))
      .toBe(agent === 'opencode' ? '' : null)
    if (agent === 'opencode') expect(draft()?.clearFields).toContain('model')
    if (agent !== 'hermes') {
      await expect(
        page.getByRole('textbox', {
          name: agent === 'opencode' ? '上下文窗口' : '模型上下文',
          exact: true,
        }),
      ).toBeDisabled()
    }
    await page.locator('.agent-preview').getByRole('button', { name: '保存', exact: true }).click()
    await expect(page.locator('.agent-preview').getByRole('status')).toHaveText('无修改')
    await page.reload()
    await page.getByRole('tab', { name: label, exact: true }).click()
    await expect(selector).toHaveValue('不设置')
    await selector.click()
    await page.getByRole('option', { name: model, exact: true }).click()
    await expect(selector).toHaveValue(model)
    expect(fixture.unexpected).toEqual([])
  })
}
