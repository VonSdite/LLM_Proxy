import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

test('响应模型与 IP 多选收起后查询一次，并随维度、明细和导出保留', async ({ page }) => {
  const fixture = await mockAdmin(page)
  await page.goto('/statistics')
  await expect(page.getByRole('region', { name: '使用趋势' })).toBeVisible()
  const count = fixture.calls.filter((call) => call.path === '/api/statistics/overview').length
  for (const [label, values] of [
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
    .toBe(count + 2)
  const check = async (path: string) => {
    await expect
      .poll(() => {
        const url = fixture.calls.filter((call) => call.path === path).at(-1)?.url
        if (!url) return null
        const params = new URL(url).searchParams
        return [params.getAll('response_model'), params.getAll('ip_address')]
      })
      .toEqual([
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
  for (const label of ['输入 Token', '输出 Token', '缓存读取 Token', '缓存命中率', '总 Token']) {
    await expect(
      page
        .getByRole('columnheader')
        .filter({ has: page.getByRole('button', { name: label, exact: true }) }),
    ).toBeVisible()
  }
  await page.getByRole('button', { name: '输出 Token', exact: true }).click()
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
  await expect(page.getByRole('columnheader', { name: '输入 Token' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: '请求数' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'smart → provider/a', exact: true })).toBeVisible()
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
  }
  expect(fixture.unexpected).toEqual([])
})

test('全部用量维度显示分组上游成功率、明细计数与合计', async ({ page }) => {
  const fixture = await mockAdmin(page)
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
  await expect(chart.locator('.ui-trend-target')).toHaveCount(1)
  await chart.getByRole('tab', { name: '缓存命中率', exact: true }).click()
  await expect(chart.locator('.ui-trend-target')).toHaveAttribute('aria-label', /40\.0%/)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
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
  await expect(page.getByRole('cell', { name: '—', exact: true })).toHaveCount(1)
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

test('请求与响应模型分列，同名映射保留目标且筛选保持请求模型', async ({ page }) => {
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
          username: 'local',
          ip_address: '127.0.0.1',
          outcome: 'success',
        })),
      },
    }),
  )
  await page.goto('/statistics')
  await page.getByRole('tab', { name: '请求明细', exact: true }).click()
  const rows = page.locator('tbody tr')
  const firstFlow = rows.nth(0).locator('.statistics-model-flow')
  await expect(firstFlow).toHaveText('same→same')
  await firstFlow.getByRole('button', { name: 'same', exact: true }).hover()
  await expect(page.getByRole('tooltip')).toContainText('请求模型：same')
  await firstFlow.locator('.statistics-model-name').nth(0).hover()
  await expect(page.getByRole('tooltip')).toContainText('目标模型：same')
  await expect(rows.nth(0).getByRole('cell', { name: 'same', exact: true })).toHaveText('same')
  await expect(rows.nth(1).locator('.statistics-model-flow')).toHaveText('a→b')
  await expect(rows.nth(2).locator('.statistics-model-flow')).toHaveText('public')
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
  await firstFlow.getByRole('button', { name: 'same', exact: true }).click()
  await filteredRequest
})
