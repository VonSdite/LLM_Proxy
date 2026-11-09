import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

test('OAuth 网络使用统一代理认证输入，旧配置回填并保存原文密码', async ({ page }) => {
  const fixture = await mockAdmin(page)
  fixture.settings.oauth.proxy_mode = 'custom'
  fixture.settings.oauth.proxy = 'https://user%3Aname:p%2540ss@proxy.example:443'
  await page.goto('/settings')
  const form = page.getByRole('form', { name: 'OAuth 网络', exact: true })
  await expect(form.getByRole('textbox', { name: '代理地址', exact: true })).toHaveValue(
    'https://proxy.example:443',
  )
  await form.getByRole('button', { name: '认证', exact: true }).click()
  const popup = page.getByRole('dialog', { name: '代理认证', exact: true })
  await expect(popup.getByRole('textbox', { name: '代理用户名', exact: true })).toHaveValue(
    'user:name',
  )
  const password = popup.getByRole('textbox', { name: '代理密码', exact: true })
  await expect(password).toHaveValue('p%40ss')
  await password.fill('new@/#?%40')
  await popup.getByRole('button', { name: '完成', exact: true }).click()
  await form.getByRole('button', { name: '保存', exact: true }).click()
  await expect
    .poll(() => fixture.settings.oauth.proxy)
    .toBe('https://user%3Aname:new%40%2F%23%3F%2540@proxy.example:443')
  await form.getByRole('button', { name: '认证', exact: true }).click()
  await popup.getByRole('button', { name: '清除', exact: true }).click()
  await popup.getByRole('button', { name: '完成', exact: true }).click()
  await form.getByRole('button', { name: '保存', exact: true }).click()
  await expect.poll(() => fixture.settings.oauth.proxy).toBe('https://proxy.example:443')
  await page.reload()
  await expect(form.getByRole('button', { name: '认证', exact: true })).toHaveAttribute(
    'aria-description',
    '未设置账号密码',
  )
  await expect(password).toHaveCount(0)
  expect(fixture.unexpected).toEqual([])
})
