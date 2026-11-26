import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

test('请求明细保留完整单行时间，自适应模型列并保留手动列宽', async ({ page }) => {
  await mockAdmin(page)
  await page.addInitScript(() => {
    const key = 'table-widths:statistics-logs'
    if (!localStorage.getItem(key)) {
      localStorage.setItem(
        key,
        JSON.stringify({
          prompt_tokens: 76,
          cache_read_input_tokens: 108,
          cache_hit_rate: 92,
          first_token_latency_ms: 80,
        }),
      )
    }
  })
  await page.setViewportSize({ width: 2560, height: 1000 })
  await page.goto('/statistics')
  await page.getByRole('tab', { name: '请求明细', exact: true }).click()
  const table = page.getByRole('table')
  const header = (name: string) =>
    table.getByRole('columnheader').filter({ has: page.getByRole('button', { name, exact: true }) })
  const unreadableHeaders = () =>
    table.getByRole('columnheader').evaluateAll((headers) =>
      headers.flatMap((cell) => {
        const content = cell.querySelector('.ui-table-header-content')!
        const label = content.querySelector('.ui-table-heading:not(.ui-table-sort)')!
        const range = document.createRange()
        range.selectNodeContents(label)
        const bounds = cell.getBoundingClientRect()
        const group = content.getBoundingClientRect()
        return range.getClientRects().length !== 1 ||
          group.left < bounds.left + 7 ||
          group.right > bounds.right - 7
          ? [label.textContent?.trim()]
          : []
      }),
    )
  await expect.poll(unreadableHeaders).toEqual([])
  const time = page.locator('tbody time').first()
  await expect(time).toHaveText('2026-11-06 10:00:00')
  expect((await time.boundingBox())!.height).toBeLessThan(24)
  expect((await header('请求时间').boundingBox())!.width).toBeLessThanOrEqual(145)
  expect(
    await time.evaluate((element) => {
      const text = element.getBoundingClientRect()
      const cell = element.closest('td')!.getBoundingClientRect()
      return text.left >= cell.left + 7 && text.right <= cell.right - 7
    }),
  ).toBe(true)
  expect((await header('结果').boundingBox())!.width).toBeLessThanOrEqual(81)
  const wide = (await header('映射模型').boundingBox())!.width
  await page.setViewportSize({ width: 1920, height: 1000 })
  await expect
    .poll(async () => (await header('映射模型').boundingBox())!.width)
    .toBeLessThanOrEqual(wide)
  expect((await time.boundingBox())!.height).toBeLessThan(24)
  await expect
    .poll(() =>
      page
        .locator('.ui-table-scroll')
        .evaluate((element) => element.scrollWidth - element.clientWidth),
    )
    .toBeLessThanOrEqual(1)
  await expect(page.getByRole('cell', { name: '300,000', exact: true })).toBeVisible()
  const inputWidth = (await header('输入Tok').boundingBox())!.width
  await page.getByRole('separator', { name: '调整输入Tok列宽', exact: true }).press('ArrowLeft')
  await expect
    .poll(async () => (await header('输入Tok').boundingBox())!.width)
    .toBeCloseTo(inputWidth, 0)
  const handle = page.getByRole('separator', { name: '调整映射模型列宽', exact: true })
  const initial = (await header('映射模型').boundingBox())!.width
  await handle.focus()
  await handle.press('ArrowRight')
  await expect
    .poll(async () => (await header('映射模型').boundingBox())!.width)
    .toBeCloseTo(initial + 16, 0)
  const manual = (await header('映射模型').boundingBox())!.width
  await page.setViewportSize({ width: 2048, height: 1000 })
  await expect
    .poll(async () => (await header('映射模型').boundingBox())!.width)
    .toBeCloseTo(manual, 0)
  await page.reload()
  await page.getByRole('tab', { name: '请求明细', exact: true }).click()
  await expect
    .poll(async () => (await header('映射模型').boundingBox())!.width)
    .toBeCloseTo(manual, 0)
  await handle.dblclick()
  await expect
    .poll(async () => (await header('映射模型').boundingBox())!.width)
    .toBeCloseTo(wide, 0)
  const requestHandle = page.getByRole('separator', { name: '调整请求模型列宽', exact: true })
  const beforeDrag = (await header('请求模型').boundingBox())!.width
  const box = (await requestHandle.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 + 32, box.y + box.height / 2, { steps: 4 })
  await page.mouse.up()
  await expect
    .poll(async () => (await header('请求模型').boundingBox())!.width)
    .toBeCloseTo(beforeDrag + 32, 0)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await expect.poll(unreadableHeaders).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('IP 按最长地址保留单行，三列模型独立收窄并随刷新重新适配', async ({ page }) => {
  const fixture = await mockAdmin(page)
  let expanded = true
  await page.route('**/api/request-logs?*', (route) =>
    route.fulfill({
      json: {
        total: 2,
        logs: [
          {
            id: 1,
            ip_address: '203.0.113.7',
            target_model_id: expanded
              ? 'provider/organization/long-mapped-model-20261125-preview'
              : 'tiny',
          },
          {
            id: 2,
            ip_address: expanded ? '2001:db8:ffff:eeee:dddd:cccc:bbbb:aaaa' : '203.0.113.8',
            target_model_id: 'tiny',
          },
        ].map((row) => ({
          ...row,
          username: '演示用户',
          request_model: 'chat',
          response_model: 'model-v1',
          start_time: '2026-11-25 10:00:00',
          outcome: 'success',
          usage_status: 'known',
          total_tokens: 1234567,
          prompt_tokens: 1200000,
          completion_tokens: 34567,
          cache_usage_status: 'known',
          cache_read_input_tokens: 1000000,
          cache_hit_rate: 5 / 6,
        })),
      },
    }),
  )
  await page.setViewportSize({ width: 3200, height: 1000 })
  await page.goto('/statistics')
  await page.getByRole('tab', { name: '请求明细', exact: true }).click()
  const width = (key: string) =>
    page
      .locator(`th[data-column-id="${key}"]`)
      .evaluate((cell) => cell.getBoundingClientRect().width)
  const rightGap = () =>
    page.getByRole('table').evaluate((table) => {
      const container = table.closest('.ui-table-scroll')!.getBoundingClientRect()
      return Math.abs(container.right - table.getBoundingClientRect().right)
    })
  // 用实际文字与表头的边界验证列宽，避免只核对组件计算值。
  const excessWidths = () =>
    page.getByRole('table').evaluate((table) =>
      ['ip_address', 'request_model', 'target_model_id', 'response_model'].map((key) => {
        const header = table.querySelector<HTMLElement>(`th[data-column-id="${key}"]`)!
        const style = getComputedStyle(header)
        const padding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight)
        const textWidths = [
          ...table.querySelectorAll<HTMLElement>(`tbody [data-content-column="${key}"]`),
        ].map((content) => {
          const range = document.createRange()
          range.selectNodeContents(content)
          return range.getBoundingClientRect().width
        })
        const expected = Math.ceil(
          Math.max(
            header.querySelector('.ui-table-header-content')!.getBoundingClientRect().width,
            ...textWidths,
          ) + padding,
        )
        return Math.abs(header.getBoundingClientRect().width - expected)
      }),
    )
  await expect.poll(async () => Math.max(...(await excessWidths()))).toBeLessThanOrEqual(1)
  await expect.poll(rightGap).toBeLessThanOrEqual(1)
  const ipWidth = await width('ip_address')
  const targetWidth = await width('target_model_id')
  expect(ipWidth).toBeGreaterThan(240)
  expect(targetWidth).toBeGreaterThan((await width('request_model')) * 2)
  expect(targetWidth).toBeGreaterThan((await width('response_model')) * 2)
  const requestWidth = await width('request_model')
  const handle = page.getByRole('separator', { name: '调整请求模型列宽', exact: true })
  await handle.press('ArrowRight')
  await expect.poll(() => width('request_model')).toBeCloseTo(requestWidth + 16, 0)
  await expect.poll(rightGap).toBeLessThanOrEqual(1)
  await page.reload()
  await page.getByRole('tab', { name: '请求明细', exact: true }).click()
  await expect.poll(() => width('request_model')).toBeCloseTo(requestWidth + 16, 0)
  await handle.dblclick()
  await expect.poll(() => width('request_model')).toBeCloseTo(requestWidth, 0)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await expect.poll(() => width('target_model_id')).toBeLessThan(targetWidth)
  await expect.poll(() => width('ip_address')).toBeCloseTo(ipWidth, 0)
  expect(
    await page.locator('.statistics-ip').evaluateAll((elements) =>
      elements.every((element) => {
        const range = document.createRange()
        range.selectNodeContents(element)
        return range.getClientRects().length === 1
      }),
    ),
  ).toBe(true)
  await page.setViewportSize({ width: 390, height: 844 })
  await expect
    .poll(() => page.locator('.ui-table-scroll').evaluate((el) => el.scrollWidth > el.clientWidth))
    .toBe(true)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.setViewportSize({ width: 3200, height: 1000 })
  expanded = false
  await page.getByRole('button', { name: '刷新统计', exact: true }).click()
  await expect(page.getByRole('cell', { name: '203.0.113.8', exact: true })).toBeVisible()
  await expect.poll(() => width('ip_address')).toBeLessThan(ipWidth)
  await expect.poll(() => width('target_model_id')).toBeLessThan(targetWidth)
  await expect.poll(async () => Math.max(...(await excessWidths()))).toBeLessThanOrEqual(1)
  await expect.poll(rightGap).toBeLessThanOrEqual(1)
  expect(fixture.unexpected).toEqual([])
})

test('映射模型、响应模型与 IP 多选收起后查询一次，并随维度、明细和导出保留', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/statistics')
  await expect(page.getByRole('region', { name: '使用趋势' })).toBeVisible()
  const count = fixture.calls.filter((call) => call.path === '/api/statistics/overview').length
  for (const [label, values] of [
    ['映射模型', ['provider/a', 'provider/b']],
    ['响应模型', ['response-a', 'response-b']],
    ['IP', ['192.168.1.10', '192.168.1.20']],
  ] as const) {
    const before = fixture.calls.filter((call) => call.path === '/api/statistics/overview').length
    await page.getByLabel(label, { exact: true }).click()
    for (const value of values) {
      const option = page.getByRole('checkbox', { name: value, exact: true })
      const box = (await option.boundingBox())!
      await option.click({ position: { x: box.width - 8, y: box.height / 2 } })
      await expect(option).toBeChecked()
      await expect(page.getByRole('button', { name: `移除 ${value}`, exact: true })).toHaveCount(1)
    }
    expect(fixture.calls.filter((call) => call.path === '/api/statistics/overview')).toHaveLength(
      before,
    )
    if (label === '响应模型') await page.getByRole('heading', { name: '统计与日志' }).click()
    else await page.keyboard.press('Escape')
    await expect
      .poll(() => fixture.calls.filter((call) => call.path === '/api/statistics/overview').length)
      .toBe(before + 1)
    for (const value of values)
      await expect(page.getByRole('button', { name: `移除 ${value}`, exact: true })).toHaveCount(1)
  }
  await expect
    .poll(() => fixture.calls.filter((call) => call.path === '/api/statistics/overview').length)
    .toBe(count + 3)
  const check = async (path: string) => {
    await expect
      .poll(() => {
        const url = fixture.calls.filter((call) => call.path === path).at(-1)?.url
        if (!url) return null
        const params = new URL(url).searchParams
        return [
          params.getAll('target_model_id'),
          params.getAll('response_model'),
          params.getAll('ip_address'),
        ]
      })
      .toEqual([
        ['provider/a', 'provider/b'],
        ['response-a', 'response-b'],
        ['192.168.1.10', '192.168.1.20'],
      ])
  }
  await check('/api/statistics/overview')
  await page.getByRole('tab', { name: '用量', exact: true }).click()
  await check('/api/statistics')
  await page.getByRole('tab', { name: '用户', exact: true }).click()
  await check('/api/statistics/user-usage-summary')
  await page.getByRole('tab', { name: 'API Key', exact: true }).click()
  await check('/api/statistics/api-key-usage-summary')
  await page.getByRole('tab', { name: '响应模型', exact: true }).click()
  await check('/api/statistics/overview')
  await page.getByRole('tab', { name: '请求明细', exact: true }).click()
  await check('/api/request-logs')
  await page.getByRole('button', { name: '导出 Excel', exact: true }).click()
  await check('/api/statistics/export')
  await page.getByRole('button', { name: '导出数据', exact: true }).click()
  await check('/api/statistics/daily-stats/export')
  await page.getByRole('tab', { name: '概览', exact: true }).click()
  await page.getByRole('tab', { name: '用量', exact: true }).click()
  await page.getByRole('tab', { name: '来源 × 模型', exact: true }).click()
  await page.getByRole('table').getByRole('button', { name: 'smart', exact: true }).click()
  await check('/api/statistics/overview')
  await page.getByRole('tab', { name: '近 7 天', exact: true }).click()
  await check('/api/statistics/overview')
  await page.getByRole('button', { name: '移除 response-a', exact: true }).click()
  await expect
    .poll(() => {
      const url = fixture.calls
        .filter((call) => call.path === '/api/statistics/overview')
        .at(-1)!.url
      return new URL(url).searchParams.getAll('response_model')
    })
    .toEqual(['response-b'])
  await expect(page.getByLabel('搜索选项')).toHaveCount(0)
  await page.getByRole('button', { name: '移除 response-b', exact: true }).click()
  await expect
    .poll(() => {
      const url = fixture.calls
        .filter((call) => call.path === '/api/statistics/overview')
        .at(-1)!.url
      return new URL(url).searchParams.getAll('response_model')
    })
    .toEqual([])
  await expect(page.getByLabel('响应模型', { exact: true })).toContainText('全部响应模型')
  await expect(page.getByRole('button', { name: '查询', exact: true })).toHaveCount(0)
  expect(fixture.unexpected).toEqual([])
})

test('日期编辑过程不查询，提交有效日期后刷新一次', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/statistics')
  await expect(page.getByRole('region', { name: '使用趋势' })).toBeVisible()
  const response = (await page.getByLabel('响应模型', { exact: true }).boundingBox())!
  const refresh = (await page.getByRole('button', { name: '刷新统计', exact: true }).boundingBox())!
  expect(refresh.x).toBeGreaterThanOrEqual(response.x + response.width)
  expect(refresh.x - response.x - response.width).toBeLessThanOrEqual(12)
  expect(Math.abs(refresh.y + refresh.height - response.y - response.height)).toBeLessThanOrEqual(1)
  const count = () =>
    fixture.calls.filter((call) => call.path === '/api/statistics/overview').length
  const before = count()
  const input = page.getByLabel('开始日期', { exact: true })
  await input.evaluate((element: HTMLInputElement) => {
    for (const value of ['2026-11-01', '2026-11-02', '2026-11-03']) {
      element.value = value
      element.dispatchEvent(new Event('input', { bubbles: true }))
    }
  })
  await expect(input).toHaveValue('2026-11-03')
  expect(count()).toBe(before)
  await input.dispatchEvent('change')
  await expect.poll(count).toBe(before + 1)
  await input.dispatchEvent('change')
  await page.getByRole('heading', { name: '统计与日志' }).click()
  expect(count()).toBe(before + 1)
  const end = page.getByLabel('结束日期', { exact: true })
  await end.fill('2026-11-01')
  await expect(page.getByText('结束日期不能早于开始日期')).toBeVisible()
  expect(count()).toBe(before + 1)
  expect(fixture.unexpected).toEqual([])
})

test('概览图表、模型下钻与请求性能使用同一筛选', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/statistics')
  await expect(page.getByRole('region', { name: '使用趋势' })).toBeVisible()
  await expect(page.getByRole('region', { name: '统计概览' })).toContainText('90.9%')
  await expect(page.getByRole('region', { name: '统计概览' })).toContainText('42.5 tok/s')
  await page.getByRole('tab', { name: '用量', exact: true }).click()
  await page.getByRole('tab', { name: '来源 × 模型', exact: true }).click()
  await page.getByRole('table').getByRole('button', { name: 'smart', exact: true }).click()
  await expect
    .poll(
      () => fixture.calls.filter((call) => call.path === '/api/statistics/overview').at(-1)?.url,
    )
    .toContain('request_model=smart')
  await expect(page.getByRole('button', { name: '移除 smart', exact: true })).toBeVisible()
  await page.getByRole('tab', { name: '请求明细', exact: true }).click()
  await expect
    .poll(() => fixture.calls.filter((call) => call.path === '/api/request-logs').at(-1)?.url)
    .toContain('request_model=smart')
  await expect(page.getByRole('cell', { name: '0.68 s', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: '42.5 tok/s', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: '成功', exact: true })).toBeVisible()
  for (const value of ['300,000', '45,000', '120,000', '40.00%', '345,000']) {
    await expect(page.getByRole('cell', { name: value, exact: true })).toBeVisible()
  }
  for (const label of ['输入Tok', '输出Tok', '缓存Tok', '缓存命中率', '总Tok']) {
    await expect(
      page
        .getByRole('columnheader')
        .filter({ has: page.getByRole('button', { name: label, exact: true }) }),
    ).toBeVisible()
  }
  await page.getByRole('button', { name: '输出Tok', exact: true }).click()
  await expect
    .poll(() => fixture.calls.filter((call) => call.path === '/api/request-logs').at(-1)?.url)
    .toContain('sort_key=completion_tokens')
  await page.getByRole('button', { name: '缓存命中率', exact: true }).click()
  await expect
    .poll(() => fixture.calls.filter((call) => call.path === '/api/request-logs').at(-1)?.url)
    .toContain('sort_key=cache_hit_rate')
  const box = (await page.locator('.ui-table-scroll').boundingBox())!
  expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
  expect(fixture.unexpected).toEqual([])
})

test('用量维度统一切换，请求模型和 IP 汇总读取最新概览数据', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/statistics')
  await page.getByRole('tab', { name: '用量', exact: true }).click()
  const dimensions = page.getByRole('tablist', { name: '汇总维度' })
  await expect(dimensions.getByRole('tab')).toHaveText([
    '来源 × 模型',
    '请求模型',
    '响应模型',
    'IP',
    '用户',
    'API Key',
  ])
  await expect(dimensions.getByRole('tab').first()).toHaveText('来源 × 模型')
  await expect(dimensions.getByRole('tab').first()).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByText('开发用户').first()).toBeVisible()
  const overviewCalls = fixture.calls.filter(
    (call) => call.path === '/api/statistics/overview',
  ).length
  await dimensions.getByRole('tab', { name: '请求模型', exact: true }).click()
  await expect(page.getByRole('columnheader', { name: '输入Tok' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: '请求数' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'smart', exact: true })).toHaveCount(3)
  await expect(page.getByRole('cell', { name: 'provider/a', exact: true })).toBeVisible()
  await page.getByRole('table').getByRole('button', { name: '映射模型', exact: true }).click()
  await expect(page.locator('tbody tr').first()).toContainText('provider/a')
  await page.getByRole('table').getByRole('button', { name: '映射模型', exact: true }).click()
  await expect(page.locator('tbody tr').first()).toContainText('provider/b')
  await expect(page.getByRole('cell', { name: 'response-b', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'response-c', exact: true })).toBeVisible()
  await page.getByRole('table').getByRole('button', { name: '响应模型', exact: true }).click()
  await expect(page.locator('tbody tr').first()).toContainText('response-a')
  await page.getByRole('table').getByRole('button', { name: '响应模型', exact: true }).click()
  await expect(page.locator('tbody tr').first()).toContainText('response-c')
  await expect(page.getByRole('columnheader', { name: 'IP', exact: true })).toHaveCount(0)
  await expect(page.getByRole('cell', { name: '合计', exact: true })).toBeVisible()
  expect(fixture.calls.filter((call) => call.path === '/api/statistics/overview')).toHaveLength(
    overviewCalls + 1,
  )
  await page.getByRole('button', { name: '导出 Excel', exact: true }).click()
  await expect
    .poll(() => fixture.calls.filter((call) => call.path === '/api/statistics/export').at(-1)?.url)
    .toContain('tab=model_usage')
  await dimensions.getByRole('tab', { name: '响应模型', exact: true }).click()
  await expect(
    page.getByRole('table').getByRole('button', { name: '请求模型', exact: true }),
  ).toHaveCount(0)
  await expect(page.locator('tbody tr')).toHaveCount(3)
  await page.getByRole('table').getByRole('button', { name: '响应模型', exact: true }).click()
  await expect(page.locator('tbody tr').first()).toContainText('response-a')
  await expect(page.locator('tfoot')).toContainText('合计')
  await page.getByRole('button', { name: '导出 Excel', exact: true }).click()
  await expect
    .poll(() => fixture.calls.filter((call) => call.path === '/api/statistics/export').at(-1)?.url)
    .toContain('tab=response_model_usage')
  await dimensions.getByRole('tab', { name: '用户', exact: true }).click()
  await expect(page.getByRole('columnheader', { name: 'IP 数' })).toBeVisible()
  await page.getByRole('tab', { name: 'IP', exact: true }).click()
  await expect(page.getByRole('cell', { name: '192.168.1.10', exact: true })).toBeVisible()
  await page.getByRole('tab', { name: 'API Key', exact: true }).click()
  await expect(page.getByText('开发 Key', { exact: true })).toBeVisible()
  await page.getByRole('tab', { name: '来源 × 模型', exact: true }).click()
  await expect(
    page
      .getByRole('columnheader')
      .filter({ has: page.getByRole('button', { name: '请求模型', exact: true }) }),
  ).toBeVisible()
  expect(fixture.unexpected).toEqual([])
})

test('往返切换统计视图和用量维度均刷新一次，保留已应用的筛选', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/statistics')
  await page.getByLabel('开始日期', { exact: true }).fill('2026-11-01')
  await page.getByLabel('结束日期', { exact: true }).fill('2026-11-14')
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
  for (const [label, path] of [
    ['用量', '/api/statistics'],
    ['请求模型', '/api/statistics/overview'],
    ['响应模型', '/api/statistics/overview'],
    ['IP', '/api/statistics/overview'],
    ['用户', '/api/statistics/user-usage-summary'],
    ['API Key', '/api/statistics/api-key-usage-summary'],
    ['来源 × 模型', '/api/statistics'],
    ['请求模型', '/api/statistics/overview'],
    ['请求明细', '/api/request-logs'],
    ['用量', '/api/statistics/overview'],
    ['概览', '/api/statistics/overview'],
    ['请求明细', '/api/request-logs'],
    ['概览', '/api/statistics/overview'],
  ]) {
    const before = fixture.calls.filter((call) => call.path === path).length
    await page.getByRole('tab', { name: label!, exact: true }).click()
    await expect
      .poll(() => fixture.calls.filter((call) => call.path === path).length)
      .toBe(before + 1)
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
    const calls = fixture.calls.filter((call) => call.path === path)
    expect(calls).toHaveLength(before + 1)
    expect(calls.at(-1)!.url).toContain('start_date=2026-11-01')
    expect(calls.at(-1)!.url).toContain('end_date=2026-11-14')
    await page.getByRole('button', { name: '刷新统计', exact: true }).click()
    await expect
      .poll(() => fixture.calls.filter((call) => call.path === path).length)
      .toBe(before + 2)
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
    expect(fixture.calls.filter((call) => call.path === path).at(-1)!.url).toBe(calls.at(-1)!.url)
  }
  expect(fixture.unexpected).toEqual([])
})

test('全部用量维度显示分组上游成功率、明细计数与合计', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.setViewportSize({ width: 1920, height: 1000 })
  await page.goto('/statistics')
  await page.getByRole('tab', { name: '用量', exact: true }).click()
  const dimensions = page.getByRole('tablist', { name: '汇总维度' })
  for (const label of ['来源 × 模型', '请求模型', '响应模型', 'IP', '用户', 'API Key']) {
    await dimensions.getByRole('tab', { name: label, exact: true }).click()
    const rate = page.locator('tbody .statistics-rate').first()
    await expect(rate).toHaveText('90.9%')
    await rate.hover()
    await expect(page.getByRole('tooltip')).toContainText(
      '上游成功 10 · 上游失败 1 · 异常 2 · 中止 0 · 未知 1',
    )
    await page.keyboard.press('Escape')
    await expect(page.locator('tfoot')).toContainText('90.9%')
    for (const width of [1920, 2560, 3200]) {
      await page.setViewportSize({ width, height: 1000 })
      await expect
        .poll(() =>
          page.getByRole('table').evaluate((table) => {
            const edge = table.closest('.ui-table-scroll')!.getBoundingClientRect().right
            return Math.max(
              ...[...table.querySelectorAll('tr')].map((row) =>
                Math.abs(row.lastElementChild!.getBoundingClientRect().right - edge),
              ),
            )
          }),
        )
        .toBeLessThanOrEqual(1)
    }
    const username = page.locator('th[data-column-id="username"]')
    if (await username.count()) expect((await username.boundingBox())!.width).toBeCloseTo(72, 0)
    const ip = page.locator('th[data-column-id="ip_address"]')
    if (await ip.count()) {
      const textWidth = await page
        .locator('.statistics-ip')
        .first()
        .evaluate((element) => element.getBoundingClientRect().width)
      expect((await ip.boundingBox())!.width).toBeCloseTo(Math.ceil(textWidth + 16), 0)
    }
    await expect
      .poll(() =>
        page.locator('th').evaluateAll((headers) =>
          headers.flatMap((cell) => {
            const content = cell.querySelector('.ui-table-header-content')!
            const label = content.querySelector('.ui-table-heading:not(.ui-table-sort)')!
            const range = document.createRange()
            range.selectNodeContents(label)
            const bounds = cell.getBoundingClientRect()
            const group = content.getBoundingClientRect()
            return range.getClientRects().length !== 1 ||
              group.left < bounds.left + 7 ||
              group.right > bounds.right - 7
              ? [label.textContent?.trim()]
              : []
          }),
        ),
      )
      .toEqual([])
    expect(
      await page
        .locator('tbody .statistics-measure, tfoot .statistics-measure')
        .evaluateAll((elements) =>
          elements.flatMap((element) => {
            const text = element.getBoundingClientRect()
            const cell = element.closest('td')!.getBoundingClientRect()
            return text.left < cell.left + 7 || text.right > cell.right - 7
              ? [element.textContent]
              : []
          }),
        ),
    ).toEqual([])
    expect(
      await page.locator('.ui-table-scroll').evaluate((el) => el.scrollWidth - el.clientWidth),
    ).toBeLessThanOrEqual(1)
    await page.getByRole('button', { name: '上游成功率', exact: true }).click()
  }
  await expect
    .poll(
      () =>
        fixture.calls.filter((call) => call.path === '/api/statistics/api-key-usage-summary').at(-1)
          ?.url,
    )
    .toContain('sort_key=upstream_success_rate')
})

test('趋势切换与单日范围保留可读数值和悬浮提示', async ({ page }) => {
  await mockAdmin(page)
  await page.goto('/statistics')
  const chart = page.getByRole('region', { name: '使用趋势' })
  await chart.getByRole('tab', { name: '上游成功率', exact: true }).click()
  const first = chart.locator('.ui-trend-target').first()
  await expect(first).toHaveAttribute('aria-label', /90\.9%/)
  await first.hover()
  await expect(page.getByRole('tooltip')).toContainText('上游成功率 90.9%')
  await page.getByRole('tab', { name: '今天', exact: true }).click()
  await expect(chart.locator('.ui-trend-target')).toHaveCount(288)
  await chart.getByRole('tab', { name: '缓存命中率', exact: true }).click()
  await chart.getByRole('tab', { name: '天', exact: true }).click()
  await expect(chart.locator('.ui-trend-target')).toHaveCount(1)
  await expect(chart.locator('.ui-trend-target')).toHaveAttribute('aria-label', /40\.0%/)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
})

test('趋势粒度按日期范围显示，手动切换保留指标与筛选', async ({ page }) => {
  await page.clock.install({ time: new Date('2026-11-22T04:00:00Z') })
  const fixture = await mockAdmin(page)
  await page.goto('/statistics')
  const chart = page.getByRole('region', { name: '使用趋势' })
  const intervals = chart.getByRole('tablist', { name: '时间粒度' })
  await expect(chart.getByRole('tab', { name: 'Token', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await expect(intervals.getByRole('tab')).toHaveText(['小时', '天'])
  await expect(intervals.getByRole('tab', { name: '小时', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await expect(chart.locator('.ui-trend-target')).toHaveCount(168)
  const count = () =>
    fixture.calls.filter((call) => call.path === '/api/statistics/overview').length
  let before = count()
  await intervals.getByRole('tab', { name: '天', exact: true }).click()
  await expect(chart.locator('.ui-trend-target')).toHaveCount(7)
  expect(count()).toBe(before + 1)
  before = count()
  await page.getByLabel('请求模型', { exact: true }).click()
  await page.getByLabel('搜索选项').fill('smart')
  await page.getByRole('checkbox', { name: 'smart', exact: true }).click()
  await page.keyboard.press('Escape')
  await expect(intervals.getByRole('tab', { name: '天', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await expect
    .poll(
      () => fixture.calls.filter((call) => call.path === '/api/statistics/overview').at(-1)?.url,
    )
    .toContain('request_model=smart')
  expect(count()).toBe(before + 1)
  expect(
    fixture.calls.filter((call) => call.path === '/api/statistics/overview').at(-1)!.url,
  ).toContain('interval=1d')
  before = count()
  await page.getByRole('tab', { name: '今天', exact: true }).click()
  await expect(intervals.getByRole('tab')).toHaveText(['5分钟', '小时', '天'])
  await expect(chart.locator('.ui-trend-target')).toHaveCount(288)
  await expect(intervals.getByRole('tab', { name: '5分钟', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  expect(count()).toBe(before + 1)
  before = count()
  await intervals.getByRole('tab', { name: '小时', exact: true }).click()
  await expect(chart.locator('.ui-trend-target')).toHaveCount(24)
  expect(count()).toBe(before + 1)
  before = count()
  await intervals.getByRole('tab', { name: '5分钟', exact: true }).click()
  await expect(chart.locator('.ui-trend-target')).toHaveCount(288)
  expect(count()).toBe(before + 1)
  await expect(chart.locator('.ui-trend-date').first()).toHaveText('11-22 00:00')
  await expect(chart.locator('.ui-trend-target').first()).toHaveAttribute(
    'aria-label',
    /2026-11-22 00:00:00 Token 144k/,
  )
  before = count()
  await page.getByRole('tab', { name: '近 7 天', exact: true }).click()
  await expect(intervals.getByRole('tab')).toHaveText(['小时', '天'])
  await expect(intervals.getByRole('tab', { name: '小时', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await expect(chart.locator('.ui-trend-target')).toHaveCount(168)
  expect(count()).toBe(before + 1)
  expect(
    fixture.calls.filter((call) => call.path === '/api/statistics/overview').at(-1)!.url,
  ).toContain('interval=1h')
  await expect(chart.getByRole('tab', { name: 'Token', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  before = count()
  await page.getByLabel('开始日期', { exact: true }).fill('2026-11-15')
  await page.getByLabel('开始日期', { exact: true }).blur()
  await expect(intervals.getByRole('tab')).toHaveText(['天'])
  await expect(intervals.getByRole('tab', { name: '天', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await expect(chart.locator('.ui-trend-target')).toHaveCount(8)
  expect(count()).toBe(before + 1)
  const latest = new URL(
    fixture.calls.filter((call) => call.path === '/api/statistics/overview').at(-1)!.url,
  )
  expect(latest.searchParams.get('interval')).toBe('1d')
  expect(latest.searchParams.get('request_model')).toBe('smart')
  await chart.getByRole('tab', { name: '缓存命中率', exact: true }).click()
  await page.getByRole('tab', { name: '近 30 天', exact: true }).click()
  await expect(intervals.getByRole('tab')).toHaveText(['天'])
  await expect(chart.locator('.ui-trend-target')).toHaveCount(30)
  await page.getByRole('tab', { name: '今天', exact: true }).click()
  await expect(intervals.getByRole('tab')).toHaveText(['5分钟', '小时', '天'])
  await expect(intervals.getByRole('tab', { name: '5分钟', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await expect(chart.locator('.ui-trend-target')).toHaveCount(288)
  await expect(chart.getByRole('tab', { name: '缓存命中率', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(fixture.unexpected).toEqual([])
})

test('历史明细不足时说明覆盖范围，按天仍可查看完整用量', async ({ page }) => {
  await mockAdmin(page)
  await page.route('**/api/statistics/overview?*', async (route) => {
    const day = new URL(route.request().url()).searchParams.get('interval') === '1d'
    await route.fulfill({
      json: {
        totals: { request_count: 10, total_tokens: 1000 },
        series_interval: day ? '1d' : '1h',
        series_partial: !day,
        series: [
          {
            date: day ? '2026-11-22' : '2026-11-22 09:00:00',
            request_count: day ? 10 : 1,
            total_tokens: day ? 1000 : 100,
          },
        ],
        models: [],
        model_flows: [],
        response_models: [],
        ips: [],
      },
    })
  })
  await page.goto('/statistics')
  const chart = page.getByRole('region', { name: '使用趋势' })
  await expect(chart).toContainText('部分历史用量仅支持按天查看')
  await chart.getByRole('tab', { name: '天', exact: true }).click()
  await expect(chart).not.toContainText('部分历史用量仅支持按天查看')
  await expect(chart.locator('.ui-trend-target')).toHaveAttribute('aria-label', /Token 1k/)
})

test('失败与未知用量保持独立，无样本的趋势不显示零命中率', async ({ page }) => {
  await mockAdmin(page)
  await page.route('**/api/statistics/overview?*', (route) =>
    route.fulfill({
      json: {
        totals: {
          request_count: 1,
          total_tokens: 0,
          success_count: 0,
          error_count: 1,
          success_rate: 0,
          cache_hit_rate: null,
          latency_sample_count: 0,
          tps_sample_count: 0,
        },
        series: [{ date: '2026-11-13', request_count: 1, total_tokens: 0, cache_hit_rate: null }],
        models: [],
        ips: [],
      },
    }),
  )
  await page.route('**/api/request-logs?*', (route) =>
    route.fulfill({
      json: {
        total: 1,
        logs: [
          {
            id: 1,
            start_time: '2026-11-13 10:00:00.136856',
            ip_address: '127.0.0.1',
            username: '-',
            request_model: 'failed-model',
            outcome: 'error',
            http_status: 502,
            error_type: 'upstream_error',
            usage_status: 'unknown',
            prompt_tokens: 0,
            completion_tokens: 0,
            total_tokens: 0,
            cache_usage_status: 'unknown',
            cache_hit_rate: null,
            first_token_latency_ms: null,
            tps: null,
            duration_ms: 1800,
          },
        ],
      },
    }),
  )
  await page.goto('/statistics')
  const chart = page.getByRole('region', { name: '使用趋势' })
  await chart.getByRole('tab', { name: '缓存命中率', exact: true }).click()
  await expect(chart).toContainText('暂无可计算数据')
  await page.getByRole('tab', { name: '请求明细', exact: true }).click()
  await expect(page.getByRole('cell', { name: '失败 502 上游错误' })).toBeVisible()
  await expect(page.getByRole('cell', { name: '未知', exact: true })).toHaveCount(6)
  await expect(page.getByRole('cell', { name: '2026-11-13 10:00:00', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: '127.0.0.1', exact: true })).toBeVisible()
  await expect(page.getByText('未登记用户')).toHaveCount(0)
  await expect(page.getByLabel('TPS 未计算原因')).toHaveText('—')
  await page.getByLabel('TPS 未计算原因').hover()
  await expect(page.getByRole('tooltip')).toContainText('请求失败或被取消')
})

test('TPS 空值解释用量与总耗时限制', async ({ page }) => {
  await mockAdmin(page)
  await page.route('**/api/request-logs?*', (route) =>
    route.fulfill({
      json: {
        total: 3,
        logs: ['missing_usage', 'invalid_duration', null].map((reason, index) => ({
          id: index + 1,
          start_time: '2026-11-14 21:35:30',
          ip_address: '127.0.0.1',
          request_model: 'test',
          outcome: 'success',
          is_stream: true,
          usage_status: reason === 'missing_usage' ? 'unknown' : 'known',
          prompt_tokens: 100,
          completion_tokens: 20,
          total_tokens: 120,
          cache_usage_status: 'known',
          cache_read_input_tokens: 80,
          cache_hit_rate: 0.8,
          first_token_latency_ms: 1000,
          tps: null,
          tps_unavailable_reason: reason,
          duration_ms: reason === 'invalid_duration' ? 0 : null,
        })),
      },
    }),
  )
  await page.goto('/statistics')
  await page.getByRole('tab', { name: '请求明细', exact: true }).click()
  const hints = page.getByLabel('TPS 未计算原因')
  await hints.nth(0).hover()
  await expect(page.getByRole('tooltip')).toContainText('未提供可用的输出 Token')
  await hints.nth(1).hover()
  await expect(page.getByRole('tooltip')).toContainText('缺少有效的请求总耗时')
  await hints.nth(2).hover()
  await expect(page.getByRole('tooltip')).toContainText('缺少有效的输出用量或总耗时')
})

test('日期快捷范围立即跟随输入，自定义日期不保留旧选中项', async ({ page }) => {
  await page.clock.install({ time: new Date('2026-11-14T02:00:00Z') })
  const fixture = await mockAdmin(page)
  await page.goto('/statistics')
  const presets = page.getByRole('tablist', { name: '日期快捷范围' })
  await expect(presets.getByRole('tab', { name: '近 7 天' })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await expect(page.getByLabel('开始日期', { exact: true })).toHaveValue('2026-11-08')
  await expect(page.getByLabel('结束日期', { exact: true })).toHaveValue('2026-11-14')
  const initialQuery = fixture.calls.find((call) => call.path === '/api/statistics/overview')!.url
  expect(initialQuery).toContain('start_date=2026-11-08')
  expect(initialQuery).toContain('end_date=2026-11-14')
  await presets.getByRole('tab', { name: '近 30 天' }).click()
  await expect(page.getByLabel('开始日期', { exact: true })).toHaveValue('2026-10-16')
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
  const count = fixture.calls.filter((call) => call.path === '/api/statistics/overview').length
  await page.getByLabel('开始日期', { exact: true }).fill('2026-11-12')
  await expect(presets.locator('[aria-selected="true"]')).toHaveCount(0)
  await expect
    .poll(() => fixture.calls.filter((call) => call.path === '/api/statistics/overview').length)
    .toBeGreaterThan(count)
  await expect(presets.locator('[aria-selected="true"]')).toHaveCount(0)
  await presets.getByRole('tab', { name: '今天', exact: true }).click()
  await expect(page.getByLabel('开始日期', { exact: true })).toHaveValue('2026-11-14')
  await expect(presets.getByRole('tab', { name: '今天', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await page.getByLabel('结束日期', { exact: true }).fill('2026-11-13')
  await expect(presets.locator('[aria-selected="true"]')).toHaveCount(0)
})

test('请求明细的说明在排序之前，结果解释四种实际状态', async ({ page }) => {
  await mockAdmin(page)
  await page.goto('/statistics')
  await page.getByRole('tab', { name: '请求明细', exact: true }).click()
  for (const label of ['结果', '首字延迟', 'TPS', '耗时']) {
    const header = page
      .getByRole('columnheader')
      .filter({ has: page.getByRole('button', { name: label, exact: true }) })
    const heading = header.getByRole('button', { name: label, exact: true })
    const help = header.getByRole('button', { name: `${label}说明`, exact: true })
    const sort = header.getByRole('button', { name: `按${label}排序`, exact: true })
    expect((await heading.boundingBox())!.x).toBeLessThan((await help.boundingBox())!.x)
    expect((await help.boundingBox())!.x).toBeLessThan((await sort.boundingBox())!.x)
    const before = await header.getAttribute('aria-sort')
    await help.hover()
    await expect(page.locator('.ui-help-content')).toBeVisible()
    if (label === '结果') {
      await expect(page.locator('.ui-help-content')).toContainText('成功：本次请求正常结束')
      await expect(page.locator('.ui-help-content')).toContainText('失败：上游返回错误')
      await expect(page.locator('.ui-help-content')).toContainText('已取消：响应完成前连接被关闭')
      await expect(page.locator('.ui-help-content')).toContainText('未知：这条日志没有记录请求结果')
      await expect(page.locator('.ui-help-content')).not.toContainText('成功请求 ÷')
    }
    expect(await header.getAttribute('aria-sort')).toBe(before)
    await page.keyboard.press('Escape')
    await sort.click()
    await expect(header).toHaveAttribute('aria-sort', /ascending|descending/)
  }
})

test('请求、映射与响应模型分列，同名映射保留目标且筛选保持请求模型', async ({ page }) => {
  await mockAdmin(page)
  await page.route('**/api/request-logs?*', (route) =>
    route.fulfill({
      json: {
        total: 3,
        logs: [
          { id: 1, request_model: 'same', target_model_id: 'same', response_model: 'same' },
          { id: 2, request_model: 'a', target_model_id: 'b', response_model: 'a' },
          { id: 3, request_model: 'public', response_model: 'upstream' },
        ].map((row) => ({
          ...row,
          start_time: '2026-11-15 10:54:35',
          username: row.id === 3 ? null : 'local',
          ip_address: '127.0.0.1',
          outcome: 'success',
        })),
      },
    }),
  )
  await page.goto('/statistics')
  await page.getByRole('tab', { name: '请求明细', exact: true }).click()
  const rows = page.locator('tbody tr')
  const cells = rows.nth(0).getByRole('cell')
  await expect(page.getByRole('columnheader', { name: '调用来源', exact: true })).toHaveCount(0)
  await expect(cells.nth(1)).toHaveText('local')
  await expect(cells.nth(2)).toHaveText('127.0.0.1')
  await expect(cells.nth(3)).toHaveText('same')
  await expect(cells.nth(4)).toHaveText('same')
  await expect(cells.nth(5)).toHaveText('same')
  const requestModel = cells.nth(3).getByRole('button', { name: 'same', exact: true })
  await requestModel.hover()
  await expect(page.getByRole('tooltip')).toContainText('请求模型：same')
  await cells.nth(4).locator('.statistics-model-name').hover()
  await expect(page.getByRole('tooltip')).toContainText('映射模型：same')
  await expect(rows.nth(1).getByRole('cell').nth(3)).toHaveText('a')
  await expect(rows.nth(1).getByRole('cell').nth(4)).toHaveText('b')
  await expect(rows.nth(2).getByRole('cell').nth(1)).toHaveText('-')
  await expect(rows.nth(2).getByRole('cell').nth(3)).toHaveText('public')
  await expect(rows.nth(2).getByRole('cell').nth(4)).toHaveText('-')
  await expect(rows.nth(2).getByRole('cell', { name: 'upstream', exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('tooltip')).toHaveCount(0)
  const filteredRequest = page.waitForRequest((request) => {
    const url = new URL(request.url())
    return (
      url.pathname === '/api/statistics/overview' &&
      url.searchParams.get('request_model') === 'same'
    )
  })
  await requestModel.click()
  await filteredRequest
})
