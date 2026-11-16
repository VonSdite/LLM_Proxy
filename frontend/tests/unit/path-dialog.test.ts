import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { FileSnapshot } from '@/api/agents'
import PathDialog from '@/features/agents/PathDialog.vue'
const file: FileSnapshot = {
  path: '/srv/custom/config.toml',
  defaultPath: '/home/test/.codex/config.toml',
  defaultDirectory: '/home/test/.codex',
  customPath: '/srv/custom/config.toml',
  exists: true,
  managed: {},
  version: 'v1',
  parseError: '',
}
afterEach(() => {
  document.body.innerHTML = ''
})
describe('自定义配置路径', () => {
  it('Codex 使用完整文件路径，保存失败保留输入并标记字段', async () => {
    const save = vi.fn().mockRejectedValue(new Error('路径不可访问'))
    const wrapper = mount(PathDialog, {
      props: { open: true, agent: 'codex', file, save },
      attachTo: document.body,
    })
    await flushPromises()
    const input = document.querySelector<HTMLInputElement>('input')!
    expect(document.body.textContent).not.toContain('默认：')
    expect(input.placeholder).toBe('/home/test/.codex/config.toml')
    input.value = ' /srv/new/config.toml '
    input.dispatchEvent(new Event('input', { bubbles: true }))
    const button = [...document.querySelectorAll('button')].find(
      (item) => item.textContent?.trim() === '保存',
    )!
    button.click()
    await flushPromises()
    expect(save).toHaveBeenCalledWith('codex', '/srv/new/config.toml')
    expect(input.value).toBe(' /srv/new/config.toml ')
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(wrapper.emitted('update:open')).toBeUndefined()
    wrapper.unmount()
  })
  it('认证文件独立显示路径，恢复默认仅发送当前目标', async () => {
    const save = vi.fn().mockResolvedValue(undefined)
    const wrapper = mount(PathDialog, {
      props: {
        open: true,
        agent: 'codexAuth',
        file: { ...file, defaultPath: '/home/test/.codex/auth.json', customPath: '' },
        save,
      },
      attachTo: document.body,
    })
    await flushPromises()
    expect(document.querySelector<HTMLInputElement>('input')!.placeholder).toBe(
      '/home/test/.codex/auth.json',
    )
    expect(document.body.textContent).toContain('Codex 认证文件（auth.json）')
    const restore = [...document.querySelectorAll('button')].find(
      (item) => item.textContent?.trim() === '恢复默认',
    )!
    restore.click()
    await flushPromises()
    expect(save).toHaveBeenCalledWith('codexAuth', '')
    expect(wrapper.emitted('update:open')).toEqual([[false]])
    wrapper.unmount()
  })
})
