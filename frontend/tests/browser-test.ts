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

// 全页截图前统一系统滚动条状态，避免 CDP 截图时临时隐藏滚动条引起宽度变化。
export const visualTest = test.extend<{ screenshotScrollbars: void }>({
  screenshotScrollbars: [
    async ({ context, page }, use) => {
      const session = await context.newCDPSession(page)
      await session.send('Emulation.setScrollbarsHidden', { hidden: true })
      try {
        await use()
      } finally {
        await session.detach()
      }
    },
    { auto: true },
  ],
})

export { expect }
