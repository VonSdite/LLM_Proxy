import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import UiTrendChart from '@/components/ui/UiTrendChart.vue'

describe('趋势图', () => {
  it.each([1, 2, 6, 7])('短日期范围的悬浮区域保持在图内（%i 天）', (count) => {
    const wrapper = mount(UiTrendChart, {
      props: {
        label: '请求',
        format: String,
        points: Array.from({ length: count }, (_, i) => ({ label: `day-${i}`, value: i + 1 })),
      },
      global: { stubs: { UiTooltip: { template: '<slot />' } } },
    })
    const rectangles = wrapper.findAll('foreignObject')
    expect(rectangles).toHaveLength(count)
    for (const rect of rectangles) {
      expect(Number(rect.attributes('x'))).toBeGreaterThanOrEqual(48)
      expect(Number(rect.attributes('x')) + Number(rect.attributes('width'))).toBeLessThanOrEqual(
        712,
      )
    }
    wrapper.unmount()
  })

  it('未知比例留下断点，不绘制成零或跨越缺失值连线', () => {
    const wrapper = mount(UiTrendChart, {
      props: {
        label: '成功率',
        format: String,
        points: [
          { label: 'one', value: 1 },
          { label: 'two', value: null },
          { label: 'three', value: 0 },
        ],
      },
      global: { stubs: { UiTooltip: { template: '<slot />' } } },
    })
    expect(wrapper.findAll('.ui-trend-dot')).toHaveLength(2)
    expect(
      wrapper.findAll('.ui-trend-line').every((line) => !line.attributes('d')?.includes('L')),
    ).toBe(true)
    expect(wrapper.findAll('.ui-trend-target')[1]!.attributes('aria-label')).toContain('未知')
    wrapper.unmount()
  })
})
