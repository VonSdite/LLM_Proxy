import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { oauthApi, type OAuthFile, type OAuthImportResult, type Quota } from '@/api/oauth'
import { ApiError, errorMessage, isAbort } from '@/api/client'
import { notify } from '@/components/ui'
import { MAX_TIMER_DELAY_MS, OAUTH_SYNC_INTERVAL_MS, quotaDue, timestamp } from './quota'

export function useOAuthFiles() {
  const files = ref<OAuthFile[]>([])
  const busy = ref('')
  const externalBusy = ref(false)
  const blocked = computed(() => !!busy.value || externalBusy.value)
  const error = ref('')
  const fileErrors = ref<Record<string, string>>({})
  const loadingFile = ref('')
  const progress = ref({ done: 0, total: 0 })
  const importResult = ref<OAuthImportResult>()
  const now = ref(Date.now())
  const lifetime = new AbortController()
  let interval: ReturnType<typeof setInterval> | undefined
  let expiryTimer: ReturnType<typeof setTimeout> | undefined
  const alive = () => !lifetime.signal.aborted
  function update(name: string, patch: Partial<OAuthFile>) {
    if (!alive()) return
    files.value = files.value.map((file) => (file.name === name ? { ...file, ...patch } : file))
  }
  function failure(name: string, reason: unknown) {
    if (!alive() || isAbort(reason)) return
    fileErrors.value[name] = errorMessage(reason)
    if (reason instanceof ApiError && reason.details.auth_failed === true)
      update(name, { availability_status: 'auth_failed', availability_status_message: '认证失败' })
  }
  function quotaChanged(name: string, quota: Quota) {
    const original = files.value.find((file) => file.name === name)
    if (!original) return
    update(name, {
      quota,
      quota_error: '',
      quota_refreshed_at: quota.refreshed_at || original.quota_refreshed_at,
      current_usage: quota.current_usage || original.current_usage,
      previous_usage: 'previous_usage' in quota ? quota.previous_usage : original.previous_usage,
      plan_type: quota.plan_type || original.plan_type,
    })
  }
  async function readList() {
    const result = await oauthApi.files(lifetime.signal)
    if (!alive()) return
    files.value = result.files
    now.value = Date.now()
  }
  async function refreshOne(name: string, cards = true) {
    if (!alive()) return false
    loadingFile.value = name
    delete fileErrors.value[name]
    let ok = true
    try {
      try {
        quotaChanged(name, await oauthApi.quota(name, lifetime.signal))
      } catch (reason) {
        ok = false
        failure(name, reason)
        const refreshed = reason instanceof ApiError ? reason.details.quota_refreshed_at : undefined
        update(name, {
          quota_error: errorMessage(reason),
          ...(typeof refreshed === 'string' ? { quota_refreshed_at: refreshed } : {}),
        })
      }
      // 额度失败后仍按既有规则查询重置卡，单个读取失败不会中止批量。
      if (cards && alive()) {
        try {
          const result = await oauthApi.cards(name, lifetime.signal)
          update(name, {
            reset_cards: result.reset_cards,
            reset_cards_refreshed_at: result.refreshed_at,
          })
        } catch (reason) {
          ok = false
          failure(name, reason)
        }
      }
      return ok
    } finally {
      if (alive()) loadingFile.value = ''
    }
  }
  async function refreshMany(names: readonly string[], cards = true) {
    const snapshot = [...new Set(names)]
    progress.value = { done: 0, total: snapshot.length }
    let failed = 0
    for (const name of snapshot) {
      if (!alive()) break
      if (!(await refreshOne(name, cards))) failed++
      if (alive()) progress.value.done++
    }
    return failed
  }
  async function run(kind: string, task: () => Promise<void>) {
    if (blocked.value || !alive()) return
    busy.value = kind
    error.value = ''
    try {
      await task()
    } catch (reason) {
      if (alive() && !isAbort(reason)) error.value = errorMessage(reason)
    } finally {
      if (alive()) {
        busy.value = ''
        loadingFile.value = ''
      }
    }
  }
  async function reload() {
    await run('loading', readList)
  }
  async function refresh(names: string[]) {
    const snapshot = [...names]
    await run('refresh', async () => {
      const failed = await refreshMany(snapshot)
      if (!alive()) return
      await readList()
      notify(
        failed ? `刷新完成，${failed} 个账号读取失败` : `已刷新 ${snapshot.length} 个账号`,
        failed ? 'warning' : 'success',
      )
    })
  }
  async function sync() {
    if (document.hidden || blocked.value || !alive()) return
    await run('sync', async () => {
      await readList()
      const names = files.value.filter((file) => quotaDue(file)).map((file) => file.name)
      for (const name of names) {
        if (document.hidden || !alive()) break
        await refreshOne(name)
      }
      if (names.length && alive()) await readList()
    })
  }
  function visibilityChanged() {
    if (!document.hidden) void sync()
  }
  async function mutate(names: string[], operation: 'enable' | 'disable' | 'delete') {
    const snapshot = [...names]
    await run(operation, async () => {
      const failures: string[] = []
      for (const name of snapshot) {
        if (!alive()) break
        try {
          if (operation === 'delete') {
            await oauthApi.remove(name, lifetime.signal)
            if (alive()) files.value = files.value.filter((file) => file.name !== name)
          } else {
            await oauthApi.enabled(name, operation === 'enable', lifetime.signal)
            update(name, { enabled: operation === 'enable', disabled: operation === 'disable' })
          }
        } catch (reason) {
          failure(name, reason)
          failures.push(`${name}：${errorMessage(reason)}`)
        }
      }
      if (!alive()) return
      if (failures.length) error.value = failures.join('\n')
      else
        notify(
          `已${operation === 'delete' ? '删除' : operation === 'enable' ? '启用' : '禁用'} ${snapshot.length} 个认证文件`,
        )
      await readList()
    })
  }
  async function exportFiles(names: string[]) {
    const snapshot = [...names]
    await run('export', () => oauthApi.export(snapshot, lifetime.signal))
  }
  async function importFiles(inputs: File[]) {
    await run('import', async () => {
      const result = await oauthApi.import(inputs, lifetime.signal)
      if (!alive()) return
      importResult.value = result
      await readList()
      const failed = await refreshMany(result.imported_files)
      if (alive()) await readList()
      if (alive())
        notify(
          `已导入 ${result.imported} 个认证文件${failed ? `，${failed} 个账号额度读取失败` : ''}`,
          result.failed || failed ? 'warning' : 'success',
        )
    })
  }
  async function loggedIn(name: string) {
    externalBusy.value = false
    await run('refresh', async () => {
      await readList()
      if (name && alive()) await refreshMany([name])
      if (alive()) await readList()
    })
  }
  async function consume(name: string, card: string) {
    await run('consume', async () => {
      loadingFile.value = name
      delete fileErrors.value[name]
      try {
        const requestId =
          crypto.randomUUID?.() ||
          `${Date.now()}-${Array.from(crypto.getRandomValues(new Uint32Array(4))).join('-')}`
        const result = await oauthApi.consume(name, card, requestId, lifetime.signal)
        if (!alive()) return
        if (result.reset_cards)
          update(name, {
            reset_cards: result.reset_cards,
            reset_cards_refreshed_at: result.reset_cards_refreshed_at,
          })
        if (result.quota) quotaChanged(name, result.quota)
        if (result.outcome === 'nothing_to_reset') {
          notify('当前额度无需重置，未消费重置卡')
          return
        }
        if (result.outcome === 'no_credit') {
          fileErrors.value[name] = '所选重置卡已不可用，请刷新额度'
          return
        }
        if (!['reset', 'already_redeemed'].includes(result.outcome))
          throw new Error('上游返回未知重置结果')
        await readList()
        const ok = await refreshOne(name, false)
        if (result.reset_cards_error) fileErrors.value[name] = result.reset_cards_error
        if (alive())
          notify(
            ok && !result.reset_cards_error
              ? '重置卡已使用，额度已刷新'
              : '重置卡已使用，部分状态读取失败',
            ok ? 'success' : 'warning',
          )
      } catch (reason) {
        failure(name, reason)
        throw reason
      }
    })
  }
  function scheduleExpiry() {
    clearTimeout(expiryTimer)
    const current = Date.now()
    const expiries = files.value
      .flatMap((file) => file.reset_cards.map((card) => timestamp(card.expires_at)))
      .filter((value): value is number => value !== null && value > current)
    if (!expiries.length) return
    const delay = Math.min(MAX_TIMER_DELAY_MS, Math.max(1, Math.min(...expiries) - current))
    expiryTimer = setTimeout(() => {
      now.value = Date.now()
      scheduleExpiry()
    }, delay)
  }
  watch(files, scheduleExpiry)
  onMounted(() => {
    void reload()
    interval = setInterval(() => void sync(), OAUTH_SYNC_INTERVAL_MS)
    document.addEventListener('visibilitychange', visibilityChanged)
  })
  onBeforeUnmount(() => {
    lifetime.abort()
    clearInterval(interval)
    clearTimeout(expiryTimer)
    document.removeEventListener('visibilitychange', visibilityChanged)
  })
  return {
    files,
    busy,
    externalBusy,
    blocked,
    error,
    fileErrors,
    loadingFile,
    progress,
    importResult,
    now,
    reload,
    refresh,
    sync,
    mutate,
    exportFiles,
    importFiles,
    loggedIn,
    consume,
  }
}
