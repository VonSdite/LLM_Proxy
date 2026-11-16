import { reactive } from 'vue'
import type { AgentDefinition } from '@/api/agents'

const fallback: AgentDefinition[] = [
  { id: 'claude', label: 'Claude', files: ['claude'], legacy: true },
  { id: 'codex', label: 'Codex', files: ['codex', 'codexAuth'], legacy: true },
  { id: 'opencode', label: 'Opencode v1', files: ['opencode'], legacy: true },
]
export const AGENT_DEFINITIONS = reactive<AgentDefinition[]>([...fallback])
export function installAgentDefinitions(definitions?: AgentDefinition[]) {
  AGENT_DEFINITIONS.splice(0, AGENT_DEFINITIONS.length, ...(definitions ?? fallback))
}
export function agentDefinition(id: string) {
  return AGENT_DEFINITIONS.find((agent) => agent.id === id)
}
