import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import MachineDialog from '@/features/agents/MachineDialog.vue'

afterEach(() => {
  document.body.innerHTML = ''
})
describe('SSH 机器表单', () => {
  it('关闭已有代理认证时显式清除会话凭据', async () => {
    const save = vi.fn().mockResolvedValue('remote')
    const wrapper = mount(MachineDialog, {
      props: {
        open: true,
        save,
        machine: {
          id: 'remote',
          name: 'remote',
          host: 'server.test',
          port: '22',
          user: 'dev',
          local: false,
          connected: false,
          proxy: { mode: 'custom', url: 'http://u:p@proxy.test:8080' },
        },
      },
      attachTo: document.body,
    })
    await flushPromises()
    const button = (name: string) =>
      [...document.querySelectorAll<HTMLButtonElement>('button')].find(
        (item) => item.textContent?.trim() === name,
      )!
    button('认证').click()
    await flushPromises()
    button('清除').click()
    await flushPromises()
    button('完成').click()
    await flushPromises()
    button('保存').click()
    await flushPromises()
    expect(save).toHaveBeenCalledWith(
      expect.objectContaining({
        proxy: { mode: 'custom', url: 'http://proxy.test:8080', clearCredentials: true },
      }),
      'remote',
    )
    wrapper.unmount()
  })

  it('缺失主机和用户时标记对应字段并阻止请求', async () => {
    const save = vi.fn()
    const wrapper = mount(MachineDialog, { props: { open: true, save }, attachTo: document.body })
    await flushPromises()
    const button = [...document.querySelectorAll<HTMLButtonElement>('button')].find(
      (item) => item.textContent?.trim() === '保存并连接',
    )!
    button.click()
    await flushPromises()
    expect(save).not.toHaveBeenCalled()
    expect(document.querySelectorAll('input[aria-invalid="true"]')).toHaveLength(2)
    expect(document.body.textContent).toContain('请填写有效的 IP 或主机名')
    expect(document.body.textContent).toContain('请填写 SSH 用户名')
    wrapper.unmount()
  })
})
