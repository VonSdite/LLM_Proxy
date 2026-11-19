import type { Component } from 'vue'
import type { Bootstrap } from '@/api/session'
import type { IconName } from '@/components/ui'

/** 扩展页面组件必须以默认导出的 Vue 组件形式加载。 */
export type PageComponentLoader = () => Promise<{ default: Component }>

/** 侧边栏入口描述。 */
export interface PageNavigation {
  label: string
  icon: IconName
  order: number
}

interface PageDefinitionBase {
  id: string
  title: string
  component: PageComponentLoader
  navigation?: PageNavigation
  visible?: (context: Bootstrap) => boolean
}

/** 扩展作者声明的页面信息，路径由宿主统一生成。 */
export type ExtensionPageDefinition = PageDefinitionBase

/** 内置页面保留现有显式路径和别名。 */
export interface BuiltinPageDefinition extends PageDefinitionBase {
  path: string
  aliases?: readonly string[]
}

/** 注册表内部统一使用的页面描述。 */
export interface AdminPageDefinition extends PageDefinitionBase {
  kind: 'builtin' | 'extension'
  path: string
  aliases: readonly string[]
}

/** 扩展页面统一挂载在专用宿主路由下。 */
export const EXTENSION_ROUTE_NAME = 'admin:extension'

/** 内置页面的路由名称由页面 ID 唯一确定。 */
export function adminPageRouteName(page: AdminPageDefinition): string {
  return page.kind === 'extension' ? EXTENSION_ROUTE_NAME : `admin:${page.id}`
}

/** 规范化扩展页面，扩展不能自行占用核心页面路径。 */
export function defineExtensionPage(page: ExtensionPageDefinition): AdminPageDefinition {
  return {
    ...page,
    kind: 'extension',
    path: `/extensions/${page.id}`,
    aliases: [],
  }
}

/** 规范化内置页面，保留既有 URL 和别名。 */
export function defineBuiltinPage(page: BuiltinPageDefinition): AdminPageDefinition {
  return {
    ...page,
    kind: 'builtin',
    aliases: page.aliases || [],
  }
}
