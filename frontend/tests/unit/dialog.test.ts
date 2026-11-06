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
    document.querySelector<HTMLButtonElement>('button[aria-label="关闭"]')!.click()
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
    wrapper.unmount()
  })
})
