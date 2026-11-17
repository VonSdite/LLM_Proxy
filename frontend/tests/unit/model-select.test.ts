import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { UiCombobox, type SelectOption } from '@/components/ui'
import ModelSelect from '@/features/shared/ModelSelect.vue'

describe('ModelSelect', () => {
  it('无 Provider 前缀的模型优先，1M 变体保持相邻', () => {
    const wrapper = mount(ModelSelect, {
      props: {
        modelValue: '',
        models: ['main/gpt', 'glm-5.3', 'api/qwen', 'gpt-5.6-luna'],
        ownService: true,
        largeContext: true,
      },
    })
    const options = wrapper.findComponent(UiCombobox).props('options') as SelectOption[]
    expect(options.map((option) => option.value)).toEqual([
      'glm-5.3',
      'glm-5.3[1m]',
      'gpt-5.6-luna',
      'gpt-5.6-luna[1m]',
      'main/gpt',
      'main/gpt[1m]',
      'api/qwen',
      'api/qwen[1m]',
    ])
  })
})
