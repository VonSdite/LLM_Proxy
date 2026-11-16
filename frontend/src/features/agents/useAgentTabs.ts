import { computed, ref, watch, type Ref } from 'vue'
import type { AgentId } from '@/api/agents'
import { moveItem, type RowMove } from '@/features/shared/order'
import { AGENT_DEFINITIONS } from './registry'

const STORAGE_KEY = 'agent-tab-preferences'
const ACTIVE_KEY = 'agent-active-tab'
interface TabPreference {
  id: AgentId
  visible: boolean
}

export function normalizeAgentTabs(value: unknown): TabPreference[] {
  const ids = AGENT_DEFINITIONS.map((agent) => agent.id)
  const result: TabPreference[] = []
  if (Array.isArray(value)) {
    for (const item of value) {
      if (item && ids.includes(item.id) && !result.some((entry) => entry.id === item.id)) {
        result.push({ id: item.id, visible: item.visible !== false })
      }
    }
  }
  for (const id of ids) {
    if (!result.some((item) => item.id === id)) result.push({ id, visible: true })
  }
  if (result.length && !result.some((item) => item.visible)) result[0]!.visible = true
  return result
}

export function useAgentTabs(activeAgent: Ref<AgentId>, ready: Ref<boolean> = ref(true)) {
  let saved: unknown
  let savedActive: string | null = null
  try {
    saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')
    savedActive = localStorage.getItem(ACTIVE_KEY)
  } catch {
    // 存储不可用或数据损坏时使用内置顺序。
  }
  const preferences = ref(normalizeAgentTabs(saved))
  watch(
    () => AGENT_DEFINITIONS.map((agent) => agent.id),
    () => {
      preferences.value = normalizeAgentTabs(saved)
    },
  )
  const items = computed(() =>
    preferences.value
      .filter((item) => AGENT_DEFINITIONS.some((agent) => agent.id === item.id))
      .map((item) => ({
        ...AGENT_DEFINITIONS.find((agent) => agent.id === item.id)!,
        visible: item.visible,
      })),
  )
  const visible = computed(() => items.value.filter((item) => item.visible))
  let restored = false
  watch(
    [visible, ready],
    () => {
      if (!ready.value) return
      if (!restored) {
        if (savedActive) activeAgent.value = savedActive
        restored = true
      }
      if (!visible.value.some((item) => item.id === activeAgent.value)) {
        activeAgent.value = visible.value[0]?.id || ''
      }
    },
    { immediate: true, flush: 'post' },
  )
  watch(activeAgent, (id) => {
    if (!restored || !id) return
    try {
      localStorage.setItem(ACTIVE_KEY, id)
    } catch {
      // 存储不可用时仍可正常切换页签。
    }
  })
  watch(
    preferences,
    () => {
      try {
        saved = preferences.value
        localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences.value))
      } catch {
        // 浏览器禁用存储时，当前页面仍可调整。
      }
    },
    { deep: true },
  )
  function setVisible(id: AgentId, value: boolean) {
    if (!value && visible.value.length === 1 && visible.value[0]!.id === id) return
    preferences.value = preferences.value.map((item) =>
      item.id === id ? { ...item, visible: value } : item,
    )
  }
  function reorder(move: RowMove) {
    preferences.value = moveItem(preferences.value, move, (item) => item.id)
  }
  function reset() {
    preferences.value = normalizeAgentTabs(null)
  }
  function apply(value: unknown) {
    preferences.value = normalizeAgentTabs(value)
  }
  return { items, visible, setVisible, reorder, reset, apply }
}
