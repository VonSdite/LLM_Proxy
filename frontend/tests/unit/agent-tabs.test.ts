import { afterEach, describe, expect, it } from 'vitest'
import { effectScope, nextTick, ref } from 'vue'
import type { AgentId } from '@/api/agents'
import { normalizeAgentTabs, useAgentTabs } from '@/features/agents/useAgentTabs'

afterEach(() => localStorage.clear())

describe('Agent 页签偏好', () => {
  it('等待定义加载后恢复选中页签，并记住后续切换', async () => {
    localStorage.setItem('agent-active-tab', 'opencode')
    const scope = effectScope()
    try {
      const ready = ref(false)
      const active = ref<AgentId>('claude')
      scope.run(() => useAgentTabs(active, ready))
      expect(active.value).toBe('claude')
      ready.value = true
      await nextTick()
      expect(active.value).toBe('opencode')
      active.value = 'codex'
      await nextTick()
      expect(localStorage.getItem('agent-active-tab')).toBe('codex')
    } finally {
      scope.stop()
    }
  })

  it('已隐藏或不存在的上次页签回退到第一个可见项', () => {
    localStorage.setItem(
      'agent-tab-preferences',
      JSON.stringify([
        { id: 'codex', visible: true },
        { id: 'claude', visible: false },
      ]),
    )
    const scope = effectScope()
    try {
      for (const saved of ['claude', 'removed-agent']) {
        localStorage.setItem('agent-active-tab', saved)
        const active = ref<AgentId>('claude')
        scope.run(() => useAgentTabs(active))
        expect(active.value).toBe('codex')
      }
    } finally {
      scope.stop()
    }
  })
  it('去掉未知和重复项，补入新增 Agent，并保证至少一项显示', () => {
    expect(
      normalizeAgentTabs([
        { id: 'codex', visible: false },
        { id: 'codex', visible: true },
        null,
        { id: 'unknown' },
        { id: 'claude', visible: false },
      ]),
    ).toEqual([
      { id: 'codex', visible: false },
      { id: 'claude', visible: false },
      { id: 'opencode', visible: true },
    ])
    const allHidden = normalizeAgentTabs(
      ['claude', 'codex', 'opencode'].map((id) => ({ id, visible: false })),
    )
    expect(allHidden.filter((item) => item.visible).map((item) => item.id)).toEqual(['claude'])
  })

  it('隐藏当前项切换到可见项，排序和显隐持久化，支持恢复默认', async () => {
    const scope = effectScope()
    try {
      const active = ref<AgentId>('claude')
      const tabs = scope.run(() => useAgentTabs(active))!
      tabs.reorder({ id: 'codex', targetId: 'claude', after: false })
      tabs.setVisible('claude', false)
      tabs.setVisible('opencode', false)
      await nextTick()
      expect(active.value).toBe('codex')
      tabs.setVisible('codex', false)
      expect(tabs.visible.value.map((item) => item.id)).toEqual(['codex'])
      const restored = scope.run(() => useAgentTabs(ref<AgentId>('claude')))!
      expect(restored.items.value).toEqual(tabs.items.value)
      tabs.reset()
      await nextTick()
      expect(tabs.visible.value.map((item) => item.id)).toEqual(['claude', 'codex', 'opencode'])
    } finally {
      scope.stop()
    }
  })

  it('损坏的存储内容回退到默认页签', () => {
    localStorage.setItem('agent-tab-preferences', '{broken')
    const scope = effectScope()
    try {
      const tabs = scope.run(() => useAgentTabs(ref<AgentId>('claude')))!
      expect(tabs.visible.value).toHaveLength(3)
    } finally {
      scope.stop()
    }
  })
})
