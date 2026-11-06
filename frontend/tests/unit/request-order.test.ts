import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { useRequest } from '@/features/shared/useRequest'

describe('异步状态隔离', () => {
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
