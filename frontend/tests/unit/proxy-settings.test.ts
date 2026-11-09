import { DOMWrapper, flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { defineComponent, ref } from 'vue'
import ProxySettings from '@/features/shared/ProxySettings.vue'

afterEach(() => {
  document.body.innerHTML = ''
})

async function setup(url = 'http://proxy.test:80') {
  const value = ref(url)
  const wrapper = mount(
    defineComponent({
      components: { ProxySettings },
      setup: () => ({ value }),
      template: '<ProxySettings mode="custom" v-model:url="value" />',
    }),
    { attachTo: document.body },
  )
  const surface = new DOMWrapper(document.body)
  const button = (name: string) =>
    surface.findAll('button').find((item) => item.text().trim() === name)!
  await button('认证').trigger('click')
  await flushPromises()
  return { wrapper, surface, button, value, proxy: wrapper.findComponent(ProxySettings) }
}

describe('代理认证交互', () => {
  it('打开小窗不产生变更，编辑后恢复原值保留原始 URL 写法', async () => {
    const original = 'http://user:p%2fss@proxy.test:80'
    const { wrapper, surface, value, proxy } = await setup(original)
    expect(proxy.emitted('update:url')).toBeUndefined()
    expect(surface.get<HTMLInputElement>('[placeholder="用户名"]').element.value).toBe('user')
    const password = surface.get<HTMLInputElement>('[placeholder="密码"]')
    expect(password.element.value).toBe('p/ss')
    await password.setValue('p%40')
    expect(value.value).toBe('http://user:p%2540@proxy.test:80')
    await password.setValue('p/ss')
    expect(value.value).toBe(original)
    wrapper.unmount()
  })

  it('完成和重开保留草稿，清除才移除输出凭据', async () => {
    const { wrapper, surface, button, value } = await setup('http://u:p@proxy.test:80')
    await button('完成').trigger('click')
    await flushPromises()
    expect(value.value).toBe('http://u:p@proxy.test:80')
    await button('认证').trigger('click')
    await flushPromises()
    expect(surface.get<HTMLInputElement>('[placeholder="密码"]').element.value).toBe('p')
    await button('清除').trigger('click')
    expect(value.value).toBe('http://proxy.test:80')
    expect(surface.get<HTMLInputElement>('[placeholder="密码"]').element.value).toBe('')
    wrapper.unmount()
  })

  it('粘贴完整地址自动拆分，普通地址变更保留认证字段', async () => {
    const { wrapper, surface, value } = await setup()
    const address = surface.get('[placeholder="socks5://127.0.0.1:1080"]')
    await address.setValue('http://user%3Aname:p%2540ss@proxy.test:80')
    expect(surface.get<HTMLInputElement>('[placeholder="用户名"]').element.value).toBe('user:name')
    expect(surface.get<HTMLInputElement>('[placeholder="密码"]').element.value).toBe('p%40ss')
    await address.setValue('socks5h://new.test:1080')
    expect(value.value).toBe('socks5h://user%3Aname:p%2540ss@new.test:1080')
    wrapper.unmount()
  })

  it('地址为空时凭据变化仍进入父级草稿并保持无效端点', async () => {
    const { wrapper, surface, value } = await setup('')
    await surface.get('[placeholder="密码"]').setValue('secret')
    expect(value.value).toBe(':secret@')
    expect(
      surface.get<HTMLInputElement>('[placeholder="socks5://127.0.0.1:1080"]').element.value,
    ).toBe('')
    wrapper.unmount()
  })
})
