import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'
import type { PreviewInput } from '@/api/agents'

test('Claude 自动记忆支持三态且预览提交正确字段', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/agents')
  const memory = page.getByRole('combobox', { name: '自动记忆', exact: true })
  const preview = () =>
    (fixture.calls.filter((call) => call.path.endsWith('/preview')).at(-1)?.body as PreviewInput)
      ?.draft
  await expect(memory).toHaveText('不设置')
  for (const [label, state] of [
    ['启用', 'enabled'],
    ['禁用', 'disabled'],
    ['不设置', 'unset'],
  ]) {
    await memory.click()
    await page.getByRole('option', { name: label, exact: true }).click()
    await expect.poll(() => preview()?.runtime.autoMemoryEnabled).toBe(state)
  }
  await expect.poll(() => preview()?.clearFields).toContain('runtime.autoMemoryEnabled')
  fixture.snapshot.files.claude!.managed.autoMemoryEnabled = false
  await page.reload()
  await expect(memory).toHaveText('禁用')
  await page.getByRole('button', { name: '自动记忆说明', exact: true }).hover()
  await expect(page.locator('.ui-help-content')).toContainText('autoMemoryEnabled')
  await expect(page.locator('.ui-help-content')).toContainText('CLAUDE.md')
})
