import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ConfigFileEditor from '@/features/shared/ConfigFileEditor.vue'
import { UiDialog } from '@/components/ui'

const editor = defineComponent({
  props: { modelValue: { type: String, default: '' }, readOnly: Boolean },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () =>
      h('textarea', {
        value: props.modelValue,
        disabled: props.readOnly,
        onInput: (event: Event) =>
          emit('update:modelValue', (event.target as HTMLTextAreaElement).value),
      })
  },
})
function button(text: string) {
  return [...document.querySelectorAll<HTMLButtonElement>('button')].find(
    (entry) => entry.textContent?.trim() === text,
  )!
}
afterEach(() => {
  document.body.innerHTML = ''
})
function harness(save = vi.fn().mockResolvedValue(undefined)) {
  const wrapper = mount(ConfigFileEditor, {
    props: {
      open: true,
      source: { path: '/config.toml', text: 'model = "before"', version: 'version-1' },
      language: 'toml',
      save,
    },
    global: { stubs: { UiCodeEditor: editor } },
    attachTo: document.body,
  })
  return { wrapper, save }
}
async function edit(text: string) {
  const input = document.querySelector('textarea')!
  input.value = text
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
}
describe('文件编辑器', () => {
  it('修改后被动关闭显示三项选择；继续编辑保留内容，放弃不保存', async () => {
    const { wrapper, save } = harness()
    await flushPromises()
    await edit('unsaved')
    await wrapper.findComponent(UiDialog).vm.requestClose()
    await flushPromises()
    expect(document.body.textContent).toContain('保存修改？')
    button('继续编辑').click()
    await flushPromises()
    expect(document.querySelector('textarea')?.value).toBe('unsaved')
    await wrapper.findComponent(UiDialog).vm.requestClose()
    await flushPromises()
    button('不保存').click()
    await flushPromises()
    expect(save).not.toHaveBeenCalled()
    expect(wrapper.emitted('update:open')?.at(-1)).toEqual([false])
    wrapper.unmount()
  })

  it('页脚关闭直接放弃草稿，不弹确认且不保存', async () => {
    const { wrapper, save } = harness()
    await flushPromises()
    await edit('unsaved')
    button('关闭').click()
    await flushPromises()
    expect(document.body.textContent).not.toContain('保存修改？')
    expect(wrapper.emitted('update:open')).toEqual([[false]])
    expect(save).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('冲突保留编辑文本与版本，重试重新执行保存', async () => {
    const save = vi
      .fn()
      .mockRejectedValueOnce(new Error('文件版本冲突'))
      .mockResolvedValueOnce(undefined)
    const { wrapper } = harness(save)
    await flushPromises()
    await edit('changed')
    button('保存').click()
    await flushPromises()
    expect(document.querySelector('textarea')?.value).toBe('changed')
    expect(document.body.textContent).toContain('文件版本冲突')
    expect(wrapper.emitted('update:open')).toBeUndefined()
    button('保存').click()
    await flushPromises()
    expect(save).toHaveBeenNthCalledWith(2, 'changed', 'version-1')
    expect(wrapper.emitted('update:open')?.at(-1)).toEqual([false])
    wrapper.unmount()
  })

  it('保存期间编辑器只读并且拒绝重复提交', async () => {
    let resolve!: () => void
    const save = vi.fn().mockImplementation(
      () =>
        new Promise<void>((done) => {
          resolve = done
        }),
    )
    const { wrapper } = harness(save)
    await flushPromises()
    await edit('changed')
    button('保存').click()
    await flushPromises()
    expect(document.querySelector('textarea')?.disabled).toBe(true)
    expect(button('关闭').disabled).toBe(true)
    button('保存').click()
    expect(save).toHaveBeenCalledOnce()
    resolve()
    await flushPromises()
    wrapper.unmount()
  })
})
