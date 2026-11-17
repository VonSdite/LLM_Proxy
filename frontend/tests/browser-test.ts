import { test as base, expect } from '@playwright/test'

const endpoint = process.env.PLAYWRIGHT_CDP_ENDPOINT

// 显式指定 CDP 时复用该浏览器；各测试仍使用独立上下文。
const connected = endpoint
  ? base.extend({
      browser: async ({ playwright }, use) => {
        const browser = await playwright.chromium.connectOverCDP(endpoint)
        try {
          await use(browser)
        } finally {
          await browser.close()
        }
      },
    })
  : base

export const test = connected.extend<{ pageErrors: void }>({
  pageErrors: [
    async ({ page }, use) => {
      const errors: string[] = []
      page.on('pageerror', (error) => errors.push(error.message))
      await use()
      expect(errors).toEqual([])
    },
    { auto: true },
  ],
})

export { expect }
