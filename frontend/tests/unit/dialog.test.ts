import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { UiDialog } from '@/components/ui'

afterEach(() => {
  document.body.innerHTML = ''
})
describe('弹窗关闭', () => {
  it('受控弹窗关闭后恢复到打开它的按钮', async () => {
    const opener = document.createElement('button')
    document.body.append(opener)
    opener.focus()
    const wrapper = mount(UiDialog, {
      props: { open: true, title: '配置文件' },
      attachTo: document.body,
    })
    await flushPromises()
    expect(document.activeElement).not.toBe(opener)
    await wrapper.setProps({ open: false })
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(document.activeElement).toBe(opener)
    wrapper.unmount()
  })
  it('业务关闭钩子可以阻止关闭并保留编辑内容', async () => {
    const beforeClose = vi.fn().mockResolvedValue(false)
    const wrapper = mount(UiDialog, {
      props: { open: true, title: '配置文件', beforeClose },
      slots: { default: '<textarea>未保存内容</textarea>' },
      attachTo: document.body,
    })
    await flushPromises()
    await wrapper.vm.requestClose()
    await flushPromises()
    expect(beforeClose).toHaveBeenCalledOnce()
    expect(wrapper.emitted('update:open')).toBeUndefined()
    expect(document.querySelector('textarea')?.value).toBe('未保存内容')
    wrapper.unmount()
  })

  it('保存期间禁用关闭；可关闭时执行一次', async () => {
    const beforeClose = vi.fn().mockResolvedValue(true)
    const wrapper = mount(UiDialog, {
      props: { open: true, title: '保存配置', busy: true, beforeClose },
      attachTo: document.body,
    })
    await flushPromises()
    const close = document.querySelector<HTMLButtonElement>('button[aria-label="关闭"]')!
    expect(close.disabled).toBe(true)
    await wrapper.setProps({ busy: false })
    close.click()
    await flushPromises()
    expect(wrapper.emitted('update:open')).toEqual([[false]])
    expect(beforeClose).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('点击顶部关闭直接放弃修改，不调用关闭确认或保存', async () => {
    const beforeClose = vi.fn().mockResolvedValue(false)
    const save = vi.fn()
    const wrapper = mount(UiDialog, {
      props: { open: true, title: '编辑配置', dirty: true, beforeClose, save },
      attachTo: document.body,
    })
    await flushPromises()
    document.querySelector<HTMLButtonElement>('button[aria-label="关闭"]')!.click()
    await flushPromises()
    expect(wrapper.emitted('update:open')).toEqual([[false]])
    expect(beforeClose).not.toHaveBeenCalled()
    expect(save).not.toHaveBeenCalled()
    expect(document.body.textContent).not.toContain('保存修改？')
    wrapper.unmount()
  })
})
