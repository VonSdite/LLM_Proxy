import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'
import { addExtensionFixtures } from '../agent-extension-fixtures'
import type { PreviewInput } from '@/api/agents'

for (const version of [1, 2]) {
  test(`Opencode V${version} 禁用服务、分享及更新设置回填与清空`, async ({ page }) => {
    const fixture = await mockAdmin(page)
    addExtensionFixtures(fixture)
    const agent = version === 1 ? 'opencode' : 'opencode_v2'
    const file = fixture.snapshot.files[version === 1 ? 'opencode' : 'opencodeV2']!
    file.exists = true
    Object.assign(
      file.managed,
      version === 1
        ? { disabled_providers: ['openai', 'anthropic'], share: 'manual', autoupdate: false }
        : { 'disabled-providers': 'openai, anthropic', share: 'manual', update: 'disable' },
    )
    await page.goto('/agents')
    await page.getByRole('tab', { name: `Opencode v${version}`, exact: true }).click()
    const disabled = page.getByRole('combobox', { name: '禁用服务', exact: true })
    await expect(page.getByRole('button', { name: '移除 openai', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '移除 anthropic', exact: true })).toBeVisible()
    await expect(page.getByRole('combobox', { name: '会话分享', exact: true })).toHaveText(
      '手动分享',
    )
    await expect(page.getByRole('combobox', { name: '更新检查', exact: true })).toHaveText('关闭')
    const draft = () =>
      (
        fixture.calls.filter((call) => call.path.endsWith(`/${agent}/preview`)).at(-1)
          ?.body as PreviewInput
      )?.draft
    await page.getByRole('button', { name: '移除 anthropic', exact: true }).click()
    await disabled.fill('gemini')
    await disabled.press('Enter')
    await expect
      .poll(() =>
        version === 1
          ? draft()?.runtime.disabledProviders
          : draft()?.values?.['disabled-providers'],
      )
      .toBe('openai, gemini')
    await page.getByRole('combobox', { name: '会话分享', exact: true }).click()
    await page.getByRole('option', { name: '禁用分享', exact: true }).click()
    await expect
      .poll(() => (version === 1 ? draft()?.runtime.share : draft()?.values?.share))
      .toBe('disabled')
    await page.getByRole('combobox', { name: '更新检查', exact: true }).click()
    await page.getByRole('option', { name: '仅通知', exact: true }).click()
    await expect
      .poll(() => (version === 1 ? draft()?.runtime.autoupdate : draft()?.values?.update))
      .toBe('notify')
    await page.getByRole('button', { name: '移除 openai', exact: true }).click()
    await page.getByRole('button', { name: '移除 gemini', exact: true }).click()
    if (version === 1)
      await expect.poll(() => draft()?.clearFields).toContain('runtime.disabledProviders')
    else await expect.poll(() => draft()?.values?.['disabled-providers']).toBeNull()
    await page.getByRole('button', { name: '禁用服务说明', exact: true }).hover()
    await expect(page.locator('.ui-help-content')).toContainText('disabled_providers')
  })
}
