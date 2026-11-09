import { defineComponent } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { oauthApi, type OAuthFile, type Quota, type ResetResult } from '@/api/oauth'
import { ApiError } from '@/api/client'
import { useOAuthFiles } from '@/features/oauth/useOAuthFiles'
import { availableCards, fileCategory, quotaDue } from '@/features/oauth/quota'

vi.mock('@/api/oauth', () => ({
  oauthApi: {
    files: vi.fn(),
    quota: vi.fn(),
    cards: vi.fn(),
    consume: vi.fn(),
    enabled: vi.fn(),
    remove: vi.fn(),
    import: vi.fn(),
    export: vi.fn(),
  },
}))
vi.mock('@/components/ui', () => ({ notify: vi.fn() }))

const current = new Date('2026-11-06T10:00:00Z').getTime()
function file(name: string, overrides: Partial<OAuthFile> = {}): OAuthFile {
  return {
    name,
    path: `/auth/${name}`,
    email: `${name}@example.test`,
    plan_type: 'plus',
    enabled: true,
    disabled: false,
    availability_status: 'available',
    availability_status_message: '',
    availability_retry_at: null,
    usage_status_message: '',
    expired: '',
    quota: null,
    quota_error: '',
    quota_refreshed_at: '',
    reset_cards: [],
    reset_cards_refreshed_at: '',
    ...overrides,
  }
}
const wrappers: VueWrapper[] = []
let state: ReturnType<typeof useOAuthFiles>
async function setup(files: OAuthFile[]) {
  vi.mocked(oauthApi.files).mockResolvedValue({ files, total: files.length, auth_dir: '/auth' })
  const wrapper = mount(
    defineComponent({
      setup() {
        state = useOAuthFiles()
        return () => ''
      },
    }),
  )
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(current)
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
  vi.mocked(oauthApi.quota).mockResolvedValue({
    windows: [],
    refreshed_at: new Date(current).toISOString(),
  })
  vi.mocked(oauthApi.cards).mockResolvedValue({
    reset_cards: [],
    refreshed_at: new Date(current).toISOString(),
  })
})
afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
  vi.clearAllMocks()
  vi.restoreAllMocks()
  vi.useRealTimers()
})
describe('OAuth 额度刷新规则与生命周期', () => {
  it('单个开关独立等待，期间暂停自动同步并保留其他账号操作能力', async () => {
    await setup([file('a'), file('b')])
    let release!: (value: { auth_file?: OAuthFile }) => void
    vi.mocked(oauthApi.enabled).mockImplementation((name) =>
      name === 'a'
        ? new Promise((resolve) => {
            release = resolve
          })
        : Promise.resolve({ auth_file: file('b', { enabled: false, disabled: true }) }),
    )
    const pending = state.setEnabled('a', false)
    expect(state.toggles.pending('a')).toBe(true)
    expect(state.toggles.value('a', true)).toBe(false)
    expect(state.toggleDisabled.value).toBe(false)
    await state.setEnabled('b', false)
    expect(state.files.value[1]?.enabled).toBe(false)
    await vi.advanceTimersByTimeAsync(60_000)
    expect(oauthApi.files).toHaveBeenCalledTimes(1)
    release({ auth_file: file('a', { enabled: false, disabled: true }) })
    await pending
    expect(state.files.value[0]?.enabled).toBe(false)
    expect(state.blocked.value).toBe(false)
    expect(oauthApi.files).toHaveBeenCalledTimes(1)
  })

  it('首次只读本地快照；每分钟仅刷新到期且尚未在重置后刷新的账号', async () => {
    const due = file('due.json', {
      quota: {
        windows: [{ label: 'Codex 5h', remaining_percent: 0, reset_at: current / 1000 - 30 }],
      },
      quota_refreshed_at: new Date(current - 60_000).toISOString(),
    })
    const fresh = file('fresh.json', {
      ...due,
      name: 'fresh.json',
      quota_refreshed_at: new Date(current).toISOString(),
    })
    await setup([due, fresh, file('no-quota.json')])
    expect(oauthApi.files).toHaveBeenCalledTimes(1)
    expect(oauthApi.quota).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(oauthApi.quota).toHaveBeenCalledExactlyOnceWith('due.json', expect.any(AbortSignal))
    expect(oauthApi.cards).toHaveBeenCalledExactlyOnceWith('due.json', expect.any(AbortSignal))
    expect(quotaDue(fresh, current)).toBe(false)
  })
  it('隐藏、外部登录和批量任务期间跳过自动同步', async () => {
    await setup([file('a')])
    const initial = vi.mocked(oauthApi.files).mock.calls.length
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    await vi.advanceTimersByTimeAsync(60_000)
    expect(oauthApi.files).toHaveBeenCalledTimes(initial)
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
    state.externalBusy.value = true
    await vi.advanceTimersByTimeAsync(60_000)
    expect(oauthApi.files).toHaveBeenCalledTimes(initial)
    state.externalBusy.value = false
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(oauthApi.files).toHaveBeenCalledTimes(initial + 1)
  })
  it('批量固定账号快照，逐个先额度后重置卡；单项失败继续后续账号', async () => {
    await setup([file('a'), file('b')])
    const calls: string[] = []
    let rejectFirst!: (error: Error) => void
    vi.mocked(oauthApi.quota).mockImplementation((name) => {
      calls.push(`quota:${name}`)
      if (name === 'a')
        return new Promise<Quota>((_resolve, reject) => {
          rejectFirst = reject
        })
      return Promise.resolve({ windows: [] })
    })
    vi.mocked(oauthApi.cards).mockImplementation(async (name) => {
      calls.push(`cards:${name}`)
      return { reset_cards: [], refreshed_at: '' }
    })
    const names = ['a', 'b']
    const pending = state.refresh(names)
    names.splice(0, names.length, 'changed')
    await vi.advanceTimersByTimeAsync(60_000)
    expect(oauthApi.files).toHaveBeenCalledTimes(1)
    rejectFirst(new ApiError('account expired', 401, { auth_failed: true }))
    await pending
    expect(calls).toEqual(['quota:a', 'cards:a', 'quota:b', 'cards:b'])
    expect(state.fileErrors.value.a).toBe('account expired')
    expect(state.progress.value).toEqual({ done: 2, total: 2 })
  })
  it('手动刷新后读取本地可用状态，认证恢复立即反映在筛选中', async () => {
    await setup([file('a', { availability_status: 'auth_failed' })])
    vi.mocked(oauthApi.files).mockResolvedValue({ files: [file('a')], total: 1, auth_dir: '/auth' })
    await state.refresh(['a'])
    expect(fileCategory(state.files.value[0]!)).toBe('available')
  })
  it('重置卡到期仅更新本地显示，远期计时器不溢出', async () => {
    const cards = [
      { id: 'soon', expires_at: (current + 2000) / 1000 },
      { id: 'later', expires_at: current + 50 * 86400_000 },
    ]
    await setup([file('a', { reset_cards: cards })])
    await vi.advanceTimersByTimeAsync(2001)
    expect(availableCards(cards, state.now.value).map((card) => card.id)).toEqual(['later'])
    expect(oauthApi.quota).not.toHaveBeenCalled()
    expect(oauthApi.cards).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(2)
  })
  it('成功消费重置卡后只再次刷新额度，避免重复读取重置卡', async () => {
    await setup([file('a')])
    vi.mocked(oauthApi.consume).mockResolvedValue({
      outcome: 'reset',
      quota: null,
      quota_error: '',
      reset_cards: [],
      reset_cards_refreshed_at: '',
      reset_cards_error: '',
      windows_reset: 1,
    })
    await state.consume('a', 'card')
    expect(oauthApi.consume).toHaveBeenCalledWith(
      'a',
      'card',
      expect.any(String),
      expect.any(AbortSignal),
    )
    expect(oauthApi.quota).toHaveBeenCalledTimes(1)
    expect(oauthApi.cards).not.toHaveBeenCalled()
  })
  it('无需重置时保留卡片，不再发起额度刷新', async () => {
    await setup([file('a', { reset_cards: [{ id: 'card', expires_at: null }] })])
    vi.mocked(oauthApi.consume).mockResolvedValue({
      outcome: 'nothing_to_reset',
      reset_cards: null,
    } as ResetResult)
    await state.consume('a', 'card')
    expect(state.files.value[0]?.reset_cards).toHaveLength(1)
    expect(oauthApi.quota).not.toHaveBeenCalled()
  })
  it('导入后仅刷新导入成功的文件', async () => {
    await setup([file('old'), file('new')])
    vi.mocked(oauthApi.import).mockResolvedValue({
      imported: 1,
      failed: 1,
      total: 2,
      imported_files: ['new'],
      failed_files: [{ name: 'bad', reason: 'invalid' }],
    })
    await state.importFiles([new File(['{}'], 'new.json')])
    expect(oauthApi.quota).toHaveBeenCalledExactlyOnceWith('new', expect.any(AbortSignal))
    expect(oauthApi.cards).toHaveBeenCalledExactlyOnceWith('new', expect.any(AbortSignal))
  })
  it('页面卸载取消请求、计时器和可见性监听，迟到结果不再更新页面', async () => {
    const wrapper = await setup([file('a')])
    let resolve!: (value: Quota) => void
    vi.mocked(oauthApi.quota).mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        }),
    )
    const pending = state.refresh(['a'])
    const signal = vi.mocked(oauthApi.quota).mock.calls[0]![1]!
    wrapper.unmount()
    expect(signal.aborted).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
    resolve({ windows: [], plan_type: 'changed' })
    await pending
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(state.files.value[0]?.plan_type).toBe('plus')
    expect(oauthApi.files).toHaveBeenCalledTimes(1)
    expect(oauthApi.cards).not.toHaveBeenCalled()
  })
})
