import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

test('额度进度在右上角持续显示，完成后收起且页面不移动', async ({ page }) => {
  await page.clock.install()
  await mockAdmin(page)
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  await page.route('**/api/oauth/codex/auth-files/account-demo.json/quota', async (route) => {
    await pending
    await route.fulfill({ json: { windows: [], refreshed_at: '2026-11-10T10:00:00Z' } })
  })
  await page.route('**/api/oauth/codex/auth-files/account-demo.json/reset-cards', (route) =>
    route.fulfill({ json: { reset_cards: [], refreshed_at: '2026-11-10T10:00:00Z' } }),
  )
  await page.goto('/oauth')
  const card = page.locator('.oauth-file').first()
  await expect(card).toBeVisible()
  const before = await card.boundingBox()
  await page.getByRole('button', { name: '刷新 account-demo.json 额度', exact: true }).click()
  const progress = page.locator('.ui-toast').filter({ hasText: '读取额度 0 / 1' })
  await expect(progress).toBeVisible()
  const position = await progress.boundingBox()
  expect(position!.y).toBeLessThan(100)
  expect(position!.x).toBeGreaterThan(page.viewportSize()!.width / 2)
  expect((await card.boundingBox())!.y).toBe(before!.y)
  await page.clock.fastForward(6000)
  await expect(progress).toBeVisible()
  release()
  await expect(progress).toHaveCount(0)
  await expect(page.locator('.ui-toast').filter({ hasText: '已刷新 1 个账号' })).toBeVisible()
})
