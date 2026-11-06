import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useAction } from '@/features/shared/useAction'
import { notify } from '@/components/ui'

vi.mock('@/components/ui', () => ({ notify: vi.fn() }))
afterEach(() => vi.clearAllMocks())
function harness(after = vi.fn()) {
  let action!: ReturnType<typeof useAction>
  const wrapper = mount(
    defineComponent({
      setup() {
        action = useAction(after)
        return () => null
      },
    }),
  )
  return { wrapper, action, after }
}
describe('页面操作生命周期', () => {
  it('一次只发送一个操作，成功后更新列表并提示', async () => {
    const { wrapper, action, after } = harness()
    let resolve!: () => void
    const save = vi.fn(
      () =>
        new Promise<void>((done) => {
          resolve = done
        }),
    )
    const pending = action.run(save, '已保存')
    expect(await action.run(save)).toBe(false)
    expect(save).toHaveBeenCalledOnce()
    resolve()
    expect(await pending).toBe(true)
    expect(after).toHaveBeenCalledOnce()
    expect(notify).toHaveBeenCalledWith('已保存')
    expect(action.busy.value).toBe(false)
    wrapper.unmount()
  })
  it('离开时取消在途请求，迟到成功不触发刷新和通知', async () => {
    const { wrapper, action, after } = harness()
    let resolve!: () => void
    let signal!: AbortSignal
    const pending = action.run((value) => {
      signal = value
      return new Promise<void>((done) => {
        resolve = done
      })
    }, '已保存')
    wrapper.unmount()
    expect(signal.aborted).toBe(true)
    resolve()
    expect(await pending).toBe(false)
    expect(after).not.toHaveBeenCalled()
    expect(notify).not.toHaveBeenCalled()
    const next = vi.fn()
    expect(await action.run(next)).toBe(false)
    expect(next).not.toHaveBeenCalled()
  })
  it('失败保留错误并可重试，取消不显示错误', async () => {
    const { wrapper, action } = harness()
    expect(await action.run(() => Promise.reject(new Error('保存失败')))).toBe(false)
    expect(action.error.value).toBe('保存失败')
    expect(await action.run(() => Promise.resolve())).toBe(true)
    expect(action.error.value).toBe('')
    expect(await action.run(() => Promise.reject(new DOMException('Canceled', 'AbortError')))).toBe(
      false,
    )
    expect(action.error.value).toBe('')
    wrapper.unmount()
  })
})
