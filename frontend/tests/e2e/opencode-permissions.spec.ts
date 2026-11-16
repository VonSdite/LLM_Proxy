import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'
import type { PreviewInput } from '@/api/agents'

test('OpenCode 操作确认策略回显、预览和清空', async ({ page }) => {
  const fixture = await mockAdmin(page)
  fixture.snapshot.files.opencode!.managed.permission = { bash: { 'git push *': 'ask' } }
  await page.goto('/agents')
  await page.getByRole('tab', { name: 'Opencode v1', exact: true }).click()
  const policy = page.getByRole('combobox', { name: '操作确认策略', exact: true })
  await expect(policy).toHaveText('文件自定义')
  const preview = () =>
    (fixture.calls.filter((call) => call.path.endsWith('/preview')).at(-1)?.body as PreviewInput)
      ?.draft
  for (const [label, value] of [
    ['跳过确认', 'allow'],
    ['每次询问', 'ask'],
    ['全部拒绝', 'deny'],
    ['不设置', ''],
  ]) {
    await policy.click()
    await page.getByRole('option', { name: label, exact: true }).click()
    await expect.poll(() => preview()?.runtime.permissionPolicy).toBe(value)
  }
  await expect.poll(() => preview()?.clearFields).toContain('runtime.permissionPolicy')
  await page.getByRole('button', { name: '操作确认策略说明', exact: true }).hover()
  await expect(page.locator('.ui-help-content')).toContainText('permission')
  await expect(page.locator('.ui-help-content')).toContainText('替换')
})
