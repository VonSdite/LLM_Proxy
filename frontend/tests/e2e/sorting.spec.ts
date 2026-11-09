import { expect, test } from '../browser-test'
import { mockProviderTable } from '../provider-fixtures'
import { mockAdmin } from '../browser-fixtures'
import type { ModelMapping } from '../../src/api/model-mappings'

test('大量 Provider 快速移动时合并落点计算，单元格不变动，取消不保存', async ({ page }) => {
  const fixture = await mockProviderTable(page)
  const sample = fixture.providers[0]!
  fixture.providers.splice(
    0,
    fixture.providers.length,
    ...Array.from({ length: 64 }, (_, i) => ({ ...sample, name: `provider-${i}` })),
  )
  await page.goto('/')
  await expect(page.locator('tbody tr')).toHaveCount(64)
  const measurements = await page.evaluate(async () => {
    const root = document.querySelector<HTMLElement>('.ui-sort-root')!
    const handle = root.querySelector<HTMLElement>('.ui-drag-handle')!
    const rows = [...root.querySelectorAll<HTMLElement>('tbody tr')]
    const original = rows.map((row) => row.getBoundingClientRect.bind(row))
    const positions = original.map((measure) => measure())
    const frame = () => new Promise(requestAnimationFrame)
    handle.dispatchEvent(
      new DragEvent('dragstart', { bubbles: true, dataTransfer: new DataTransfer() }),
    )
    await frame()
    await frame()
    let reads = 0
    let mutations = 0
    const observer = new MutationObserver((records) => {
      mutations += records.length
    })
    root.querySelectorAll('td').forEach((cell) =>
      observer.observe(cell, {
        attributes: true,
        childList: true,
        characterData: true,
        subtree: true,
      }),
    )
    rows.forEach((row, i) => {
      row.getBoundingClientRect = () => {
        reads++
        return original[i]!()
      }
    })
    try {
      for (let i = 0; i < 200; i++) {
        const rect = positions[2 + (i % 60)]!
        root.dispatchEvent(
          new DragEvent('dragover', {
            bubbles: true,
            cancelable: true,
            clientX: rect.left + 20,
            clientY: rect.bottom - 4,
          }),
        )
      }
      const beforeFrame = reads
      await frame()
      await frame()
      const marker = root.querySelector<HTMLElement>('[data-drop-position]')
      return {
        beforeFrame,
        reads,
        mutations,
        target: marker?.dataset.sortId,
        position: marker?.dataset.dropPosition,
      }
    } finally {
      observer.disconnect()
      rows.forEach((row, i) => {
        row.getBoundingClientRect = original[i]!
      })
      handle.dispatchEvent(new DragEvent('dragend', { bubbles: true }))
    }
  })
  expect(measurements.beforeFrame).toBe(0)
  expect(measurements.reads).toBeLessThanOrEqual(10)
  expect(measurements.mutations).toBe(0)
  expect(measurements.target).toBe('provider-21')
  expect(measurements.position).toBe('after')
  await expect(page.locator('[data-drop-position]')).toHaveCount(0)
  expect(fixture.calls.filter((call) => call.method !== 'GET')).toEqual([])
  expect(fixture.unexpected).toEqual([])
})

test('Provider 可从把手拖到最后一行下方，并保存完整顺序', async ({ page }) => {
  const fixture = await mockProviderTable(page)
  const writes: { names: string[] }[] = []
  await page.route('**/api/providers/order', async (route) => {
    const body = route.request().postDataJSON() as (typeof writes)[number]
    writes.push(body)
    fixture.providers.sort((a, b) => body.names.indexOf(a.name) - body.names.indexOf(b.name))
    await route.fulfill({ json: { message: 'ok' } })
  })
  await page.goto('/')
  await expect(page.getByRole('button', { name: /^(上移|下移)/ })).toHaveCount(0)
  const handle = page.getByRole('button', { name: '拖动 gateway 排序', exact: true })
  const source = (await handle.boundingBox())!
  const last = (await page.locator('tbody tr').last().boundingBox())!
  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2)
  await page.mouse.down()
  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2 + 10)
  await page.mouse.move(source.x + source.width / 2, last.y + last.height + 12, { steps: 12 })
  await expect(page.locator('tbody tr').last()).toHaveAttribute('data-drop-position', 'after')
  await page.mouse.up()
  await expect
    .poll(() => writes)
    .toEqual([{ names: ['api_responses_tt', 'claude', 'local', 'gateway', 'backup'] }])
  await expect(page.locator('.provider-name').last()).toContainText('gateway')
  expect(fixture.unexpected).toEqual([])
})

test('Provider 键盘排序保留焦点和选择，失败可重试，全部视图按状态分组排序', async ({ page }) => {
  const fixture = await mockProviderTable(page)
  let fail = true
  const writes: { names: string[] }[] = []
  await page.route('**/api/providers/order', async (route) => {
    const body = route.request().postDataJSON() as (typeof writes)[number]
    writes.push(body)
    if (fail) return route.fulfill({ status: 503, json: { error: '排序暂时无法保存' } })
    fixture.providers.sort((a, b) => body.names.indexOf(a.name) - body.names.indexOf(b.name))
    await route.fulfill({ json: { message: 'ok' } })
  })
  await page.goto('/')
  await page.getByRole('tab', { name: '全部', exact: true }).click()
  const selected = page.getByRole('checkbox', { name: '选择 gateway', exact: true })
  await selected.click()
  const handle = page.getByRole('button', { name: '拖动 gateway 排序', exact: true })
  await handle.press('End')
  await expect(page.getByRole('alert')).toContainText('排序暂时无法保存')
  await expect(page.locator('.provider-name').first()).toContainText('gateway')
  await expect(handle).toBeFocused()
  await expect(selected).toBeChecked()
  fail = false
  await handle.press('End')
  await expect(page.locator('.provider-name').nth(3)).toContainText('gateway')
  await expect(page.locator('.provider-name').last()).toContainText('backup')
  await expect(handle).toBeFocused()
  await expect(selected).toBeChecked()
  expect(writes.at(-1)?.names).toEqual(['api_responses_tt', 'claude', 'local', 'gateway', 'backup'])
  await handle.press('Home')
  await expect(page.locator('.provider-name').first()).toContainText('gateway')
  await expect(handle).toBeFocused()
  await handle.press('ArrowUp')
  expect(writes).toHaveLength(3)
  expect(fixture.unexpected).toEqual([])
})

test('模型映射列表使用同一把手，末尾落点保存顺序', async ({ page }) => {
  const fixture = await mockAdmin(page)
  const mappings: ModelMapping[] = ['first', 'middle', 'last'].map((id) => ({
    id,
    enabled: true,
    strategy: 'highest_priority',
    cooldown_seconds_on_429: 60,
    safe_desensitization_enabled: false,
    targets: [],
  }))
  await page.route('**/api/model-mappings', (route) =>
    route.fulfill({
      json: { enabled: true, available_targets: [], model_mappings: mappings },
    }),
  )
  let saved: unknown
  await page.route('**/api/model-mappings/order', async (route) => {
    const body = route.request().postDataJSON() as { ids: string[] }
    saved = body
    mappings.sort((a, b) => body.ids.indexOf(a.id) - body.ids.indexOf(b.id))
    await route.fulfill({ json: { message: 'ok' } })
  })
  await page.goto('/model-mappings')
  await expect(page.getByRole('button', { name: /^(上移|下移)/ })).toHaveCount(0)
  const handle = page.getByRole('button', { name: '拖动 first 排序', exact: true })
  const from = (await handle.boundingBox())!
  const last = (await page.locator('tbody tr').last().boundingBox())!
  await page.mouse.move(from.x + 16, from.y + 16)
  await page.mouse.down()
  await page.mouse.move(from.x + 16, from.y + 28)
  await page.mouse.move(from.x + 16, last.y + last.height + 12, { steps: 12 })
  await expect(page.locator('tbody tr').last()).toHaveAttribute('data-drop-position', 'after')
  await page.mouse.up()
  await expect.poll(() => saved).toEqual({ ids: ['middle', 'last', 'first'] })
  await expect(page.locator('tbody tr').last()).toContainText('first')
  expect(fixture.unexpected).toEqual([])
})

test('Provider 模型草稿和映射目标共用排序交互，保存包含所见顺序', async ({ page }) => {
  const fixture = await mockAdmin(page)
  fixture.provider.model_list = ['model-a', 'model-b', 'model-c']
  let savedModels: unknown
  await page.route('**/api/providers/main', async (route) => {
    savedModels = route.request().postDataJSON().model_list
    await route.fulfill({ json: fixture.provider })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '编辑 Provider main', exact: true }).click()
  const providerDialog = page.getByRole('dialog', { name: '编辑 Provider', exact: true })
  await expect(providerDialog.getByRole('button', { name: /^(上移|下移)/ })).toHaveCount(0)
  await providerDialog.getByRole('button', { name: '拖动 1 排序', exact: true }).press('End')
  await expect(providerDialog.getByRole('textbox', { name: /^模型 \d+$/ }).last()).toHaveValue(
    'model-a',
  )
  await providerDialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect.poll(() => savedModels).toEqual(['model-b', 'model-c', 'model-a'])

  const models = ['main/model-a', 'main/model-b', 'main/model-c']
  const mapping: ModelMapping = {
    id: 'ordered',
    enabled: true,
    strategy: 'highest_priority',
    cooldown_seconds_on_429: 60,
    safe_desensitization_enabled: false,
    targets: models.map((model_id) => ({ model_id, enabled: true, priority: 1 })),
  }
  await page.route('**/api/model-mappings', (route) =>
    route.fulfill({
      json: { enabled: true, available_targets: models, model_mappings: [mapping] },
    }),
  )
  let savedTargets: string[] = []
  await page.route('**/api/model-mappings/ordered', async (route) => {
    savedTargets = route
      .request()
      .postDataJSON()
      .targets.map((target: { model_id: string }) => target.model_id)
    await route.fulfill({ json: mapping })
  })
  await page.setViewportSize({ width: 1440, height: 1400 })
  await page.goto('/model-mappings')
  await page.getByRole('button', { name: '编辑模型映射 ordered', exact: true }).press('Enter')
  const dialog = page.getByRole('dialog', { name: '编辑模型映射', exact: true })
  await expect(dialog.getByRole('button', { name: /^(上移|下移)/ })).toHaveCount(0)
  const firstHandle = (await dialog
    .getByRole('button', { name: '拖动第 1 项', exact: true })
    .boundingBox())!
  const lastTarget = (await dialog.getByRole('listitem').last().boundingBox())!
  await page.mouse.move(firstHandle.x + 16, firstHandle.y + 16)
  await page.mouse.down()
  await page.mouse.move(firstHandle.x + 16, firstHandle.y + 28)
  await page.mouse.move(firstHandle.x + 16, lastTarget.y + lastTarget.height + 12, { steps: 12 })
  await expect(dialog.getByRole('listitem').last()).toHaveAttribute('data-drop-position', 'after')
  await page.mouse.up()
  await expect(dialog.getByRole('combobox', { name: '目标 3', exact: true })).toHaveValue(
    'main/model-a',
  )
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect.poll(() => savedTargets).toEqual(['main/model-b', 'main/model-c', 'main/model-a'])
  expect(fixture.unexpected).toEqual([])
})

test.describe('触屏排序', () => {
  test.use({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 1000 } })
  test('触摸把手可拖到末尾，取消手势不保存，单次落下只提交一次', async ({ page, context }) => {
    const fixture = await mockProviderTable(page)
    fixture.providers.splice(2)
    for (const provider of fixture.providers) provider.model_list = ['model']
    const writes: { names: string[] }[] = []
    await page.route('**/api/providers/order', async (route) => {
      const body = route.request().postDataJSON() as (typeof writes)[number]
      writes.push(body)
      fixture.providers.sort((a, b) => body.names.indexOf(a.name) - body.names.indexOf(b.name))
      await route.fulfill({ json: { message: 'ok' } })
    })
    await page.goto('/')
    const handle = page.getByRole('button', { name: '拖动 gateway 排序', exact: true })
    const from = (await handle.boundingBox())!
    const last = (await page.locator('tbody tr').last().boundingBox())!
    expect(from.width).toBeGreaterThanOrEqual(44)
    expect(from.height).toBeGreaterThanOrEqual(44)
    const session = await context.newCDPSession(page)
    const touch = { id: 1, x: from.x + from.width / 2, y: from.y + from.height / 2 }
    for (const cancel of [true, false]) {
      await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [touch] })
      await session.send('Input.dispatchTouchEvent', {
        type: 'touchMove',
        touchPoints: [{ ...touch, y: last.y + last.height + 12 }],
      })
      await expect(page.locator('tbody tr').last()).toHaveAttribute('data-drop-position', 'after')
      await session.send('Input.dispatchTouchEvent', {
        type: cancel ? 'touchCancel' : 'touchEnd',
        touchPoints: [],
      })
      if (cancel) {
        expect(writes).toEqual([])
        await expect(page.locator('[data-drop-position]')).toHaveCount(0)
      }
    }
    await expect.poll(() => writes).toEqual([{ names: ['api_responses_tt', 'gateway'] }])
    await expect(page.locator('.provider-name').last()).toContainText('gateway')
    await session.detach()
    expect(fixture.unexpected).toEqual([])
  })
})
