import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import MachineDialog from '@/features/agents/MachineDialog.vue'

afterEach(() => {
  document.body.innerHTML = ''
})
describe('SSH 机器表单', () => {
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
