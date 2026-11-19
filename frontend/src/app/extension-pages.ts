import {
  defineExtensionPage,
  type AdminPageDefinition,
  type ExtensionPageDefinition,
} from './page-contract'

const extensionModules = import.meta.glob<{ default: ExtensionPageDefinition }>(
  '../../../extensions/*/frontend/index.ts',
  {
    eager: true,
  },
)

function extensionId(file: string): string {
  const parts = file.split('/')
  return parts.at(-3) || ''
}

/** 发现源码扩展，并校验目录 ID 与页面声明的扩展 ID 一致。 */
function discoverExtensionPages(): AdminPageDefinition[] {
  return Object.entries(extensionModules)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([file, module]) => {
      const page = defineExtensionPage(module.default)
      const expectedId = extensionId(file)
      if (!expectedId || page.id !== expectedId) {
        throw new Error(`Extension page ID must match its directory: ${file}`)
      }
      return page
    })
}

export const localExtensionPages = discoverExtensionPages()
