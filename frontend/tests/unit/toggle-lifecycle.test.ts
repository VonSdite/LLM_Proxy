import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useToggle } from '@/features/shared/useToggle'
import { notify } from '@/components/ui'

vi.mock('@/components/ui', () => ({ notify: vi.fn() }))
afterEach(() => vi.clearAllMocks())

function harness() {
  let toggles!: ReturnType<typeof useToggle>
  const wrapper = mount(
    defineComponent({
      setup() {
        toggles = useToggle()
        return () => null
      },
    }),
  )
  return { wrapper, toggles }
}

describe('局部开关操作', () => {
  it('立即显示目标状态，同记录互斥，不同记录独立提交', async () => {
    const { wrapper, toggles } = harness()
    let release!: (value: boolean) => void
    const save = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          release = resolve
        }),
    )
    const commit = vi.fn()
    const pending = toggles.run('target-a', false, save, commit, 'mapping')
    expect(toggles.value('target-a', true)).toBe(false)
    expect(toggles.value('target-b', true)).toBe(true)
    expect(await toggles.run('target-b', false, save, commit, 'mapping')).toBe(false)
    expect(save).toHaveBeenCalledOnce()
    const independent = vi.fn()
    expect(await toggles.run('other', true, () => Promise.resolve(true), independent)).toBe(true)
    expect(independent).toHaveBeenCalledWith(true)
    expect(toggles.busy.value).toBe(true)
    release(false)
    expect(await pending).toBe(true)
    expect(commit).toHaveBeenCalledWith(false)
    expect(toggles.busy.value).toBe(false)
    wrapper.unmount()
  })

  it('失败恢复已确认值并允许重试，重复点击不会发出第二次请求', async () => {
    const { wrapper, toggles } = harness()
    const commit = vi.fn()
    expect(await toggles.run('a', false, () => Promise.reject(new Error('保存失败')), commit)).toBe(
      false,
    )
    expect(toggles.value('a', true)).toBe(true)
    expect(commit).not.toHaveBeenCalled()
    expect(notify).toHaveBeenCalledWith('保存失败', 'danger')
    expect(await toggles.run('a', false, () => Promise.resolve(), commit)).toBe(true)
    expect(commit).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('卸载取消请求，迟到成功不再写入或提示', async () => {
    const { wrapper, toggles } = harness()
    let release!: () => void
    let signal!: AbortSignal
    const commit = vi.fn()
    const pending = toggles.run(
      'a',
      false,
      (current) => {
        signal = current
        return new Promise<void>((resolve) => {
          release = resolve
        })
      },
      commit,
    )
    wrapper.unmount()
    expect(signal.aborted).toBe(true)
    release()
    expect(await pending).toBe(false)
    expect(commit).not.toHaveBeenCalled()
    expect(notify).not.toHaveBeenCalled()
    const save = vi.fn()
    expect(await toggles.run('a', true, save, commit)).toBe(false)
    expect(save).not.toHaveBeenCalled()
  })
})
