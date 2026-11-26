import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { parse } from 'yaml'
import type { AgentDefinition, AgentField } from '../src/api/agents'
import type { mockAdmin } from './browser-fixtures'

export function extensionDefinitions(): AgentDefinition[] {
  const root = new URL('../../agents/', import.meta.url)
  return readdirSync(root, { withFileTypes: true })
    .filter(
      (item) =>
        item.isDirectory() &&
        !item.name.startsWith('_') &&
        existsSync(new URL(`${item.name}/agent.yaml`, root)),
    )
    .map((item) => {
      const source = parse(readFileSync(new URL(`${item.name}/agent.yaml`, root), 'utf8'))
      const groups = Object.keys(source.groups || {})
      const fields = (source.fields || []) as AgentField[]
      fields.sort((a, b) => groups.indexOf(a.group || '') - groups.indexOf(b.group || ''))
      return {
        id: source.id,
        label: source.name,
        legacy: source.legacy || false,
        files: Object.keys(source.files),
        fields,
        help: source.help || [],
        groups: source.groups || {},
        fileFormats: Object.fromEntries(
          Object.entries(source.files).map(([id, file]) => [
            id,
            (file as { format: 'yaml' }).format,
          ]),
        ),
        order: source.order || 100,
      }
    })
    .sort((a, b) => a.order - b.order)
}

export function addExtensionFixtures(fixture: Awaited<ReturnType<typeof mockAdmin>>) {
  fixture.agent.agents = extensionDefinitions()
  for (const agent of fixture.agent.agents) {
    for (const target of agent.files) {
      if (fixture.snapshot.files[target]) continue
      const path =
        target === 'opencodeV2'
          ? '/home/test/.config/opencode/opencode.json'
          : `/home/test/.${agent.id}/config.yaml`
      fixture.snapshot.files[target] = {
        path,
        defaultPath: path,
        customPath: '',
        exists: false,
        managed: {},
        version: 'missing',
        parseError: '',
      }
    }
  }
}
