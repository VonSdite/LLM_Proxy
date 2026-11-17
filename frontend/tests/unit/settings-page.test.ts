import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import { settingsApi, type SystemSettings } from '@/api/settings'
import SettingsPage from '@/pages/SettingsPage.vue'

function fixture(): SystemSettings {
  return {
    server: { host: '127.0.0.1', port: 8080 },
    admin: { username: '', password: '' },
    client_ip: { real_ip_enabled: false, real_ip_header: 'X-Forwarded-For' },
    logging: { path: 'logs', level: 'INFO', llm_request_debug_enabled: false },
    llm_recording: { enabled: false, directory: '/var/lib/llm-proxy/llm-records' },
    oauth: {
      enabled: false,
      proxy_mode: 'direct',
      proxy: '',
      verify_ssl: false,
      safe_desensitization_enabled: false,
    },
    api_keys: { enabled: false },
    model_mapping: { enabled: true },
    auth_enabled: false,
  }
}

afterEach(() => vi.restoreAllMocks())

describe('LLM 数据记录设置', () => {
  it('开关立即保存，目录结束编辑后保存', async () => {
    const current = fixture()
    vi.spyOn(settingsApi, 'get').mockResolvedValue(structuredClone(current))
    const save = vi.spyOn(settingsApi, 'save').mockImplementation(async (_group, body) => {
      Object.assign(current, body)
      return { settings: structuredClone(current) }
    })
    const wrapper = mount(SettingsPage)
    await flushPromises()
    const section = wrapper.get('form[aria-label="LLM 数据记录"]')

    expect(section.find('input').exists()).toBe(false)
    await section.get('[role="switch"]').trigger('click')
    await flushPromises()
    expect(save).toHaveBeenLastCalledWith(
      'llm-recording',
      { llm_recording: { enabled: true, directory: '/var/lib/llm-proxy/llm-records' } },
      expect.anything(),
    )

    const directory = section.get('input')
    ;(directory.element as HTMLInputElement).value = '/srv/training-records'
    await directory.trigger('input')
    expect(save).toHaveBeenCalledTimes(1)
    await directory.trigger('change')
    await flushPromises()
    expect(save).toHaveBeenLastCalledWith(
      'llm-recording',
      { llm_recording: { enabled: true, directory: '/srv/training-records' } },
      expect.anything(),
    )
    expect(current.llm_recording).toEqual({ enabled: true, directory: '/srv/training-records' })
    wrapper.unmount()
  })

  it('保存失败保留目录草稿，空目录显示字段错误且不提交', async () => {
    const current = fixture()
    current.llm_recording.enabled = true
    vi.spyOn(settingsApi, 'get').mockResolvedValue(structuredClone(current))
    const save = vi
      .spyOn(settingsApi, 'save')
      .mockRejectedValue(new ApiError('Directory is not writable', 400))
    const wrapper = mount(SettingsPage)
    await flushPromises()
    const section = wrapper.get('form[aria-label="LLM 数据记录"]')
    const directory = section.get('input')

    ;(directory.element as HTMLInputElement).value = '/srv/unwritable'
    await directory.trigger('input')
    await directory.trigger('change')
    await flushPromises()
    expect(section.text()).toContain('未保存：Directory is not writable')
    expect((directory.element as HTMLInputElement).value).toBe('/srv/unwritable')
    expect(current.llm_recording.directory).toBe('/var/lib/llm-proxy/llm-records')

    ;(directory.element as HTMLInputElement).value = ''
    await directory.trigger('input')
    await directory.trigger('change')
    await flushPromises()
    expect(section.text()).toContain('请输入保存目录')
    expect(directory.attributes('aria-invalid')).toBe('true')
    expect(save).toHaveBeenCalledTimes(1)

    ;(directory.element as HTMLInputElement).value = 'data/records'
    await directory.trigger('input')
    await directory.trigger('change')
    await flushPromises()
    expect(section.text()).toContain('请输入绝对路径')
    expect(save).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })
})
