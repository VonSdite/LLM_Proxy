import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { useRequest } from '@/features/shared/useRequest'

describe('异步状态隔离', () => {
  it('已确认写入覆盖在途旧快照，后续读取可接收新的服务端状态', async () => {
    const resolves: ((value: { id: number; enabled: boolean }[]) => void)[] = []
    let request!: ReturnType<typeof useRequest<{ id: number; enabled: boolean }[]>>
    const wrapper = mount(
      defineComponent({
        setup() {
          request = useRequest(() => new Promise((resolve) => resolves.push(resolve)))
          return () => null
        },
      }),
    )
    const initial = request.refresh()
    resolves[0]!([{ id: 1, enabled: true }])
    await initial
    const stale = request.refresh()
    request.patch((rows) => rows.map((row) => (row.id === 1 ? { ...row, enabled: false } : row)))
    expect(request.data.value?.[0]?.enabled).toBe(false)
    resolves[1]!([
      { id: 1, enabled: true },
      { id: 2, enabled: true },
    ])
    await stale
    expect(request.data.value).toEqual([
      { id: 1, enabled: false },
      { id: 2, enabled: true },
    ])
    const current = request.refresh()
    resolves[2]!([{ id: 1, enabled: true }])
    await current
    expect(request.data.value?.[0]?.enabled).toBe(true)
    wrapper.unmount()
    request.patch(() => [])
    expect(request.data.value).toHaveLength(1)
  })

  it('迟到响应不覆盖新状态，卸载取消在途请求', async () => {
    const resolves: ((value: string) => void)[] = []
    const signals: AbortSignal[] = []
    let request!: ReturnType<typeof useRequest<string>>
    const wrapper = mount(
      defineComponent({
        setup() {
          request = useRequest((signal) => {
            signals.push(signal)
            return new Promise<string>((resolve) => resolves.push(resolve))
          })
          return () => request.data.value || ''
        },
      }),
    )
    void request.refresh()
    void request.refresh()
    expect(signals[0]?.aborted).toBe(true)
    resolves[1]!('new')
    await flushPromises()
    resolves[0]!('old')
    await flushPromises()
    expect(wrapper.text()).toBe('new')
    void request.refresh()
    wrapper.unmount()
    expect(signals[2]?.aborted).toBe(true)
    resolves[2]!('discarded')
  })
})
