import { describe, expect, it } from 'vitest'
import { entryDraft, entryErrors, entryPayload, entriesYaml } from '@/features/providers/auth-entry'
describe('认证条目草稿', () => {
  it('编辑限制与请求头时保留 ID，提交不携带 UI 字段', () => {
    const original = {
      id: 'entry-1',
      enabled: false,
      headers: { Authorization: 'Bearer secret' },
      max_concurrency: 3,
      request_quota_per_day: 10,
    }
    const draft = entryDraft(original)
    draft.token_quota_per_minute = 500
    const payload = entryPayload(draft)
    expect(payload).toMatchObject({ ...original, token_quota_per_minute: 500 })
    expect(payload).not.toHaveProperty('key')
    expect(entriesYaml([payload])).toContain('"Authorization": "Bearer secret"')
  })
  it('大小写重复请求头、重复 Entry ID 和非法额度均对应字段错误', () => {
    const a = entryDraft({
      id: 'same',
      enabled: true,
      headers: { Authorization: 'one', authorization: 'two' },
      token_quota_per_day: 0,
    })
    const b = entryDraft({ id: 'same', enabled: true, headers: { test: 'value' } })
    expect(entryErrors(a, [a, b])).toHaveProperty('id')
    expect(entryErrors(a, [a, b])).toHaveProperty('headers')
    expect(entryErrors(a, [a, b])).toHaveProperty('token_quota_per_day')
  })
})
