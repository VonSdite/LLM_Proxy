import type { AdminPageDefinition, PageNavigation } from './page-contract'

export type NavigationPage = AdminPageDefinition & { navigation: PageNavigation }

const PAGE_ID = /^[a-z][a-z0-9_-]{0,63}$/
const RESERVED_BUILTIN_PATHS = new Set(['/login', '/logout', '/dev/components'])
const RESERVED_BUILTIN_PREFIXES = ['/api/', '/v1/', '/static/']

/** 页面注册表拒绝冲突和越界路径，启动阶段直接暴露配置错误。 */
export function validatePageDefinitions(definitions: AdminPageDefinition[]): AdminPageDefinition[] {
  const ids = new Set<string>()
  const paths = new Set<string>()
  for (const page of definitions) {
    if (!PAGE_ID.test(page.id)) {
      throw new Error(`Invalid admin page ID: ${page.id}`)
    }
    if (ids.has(page.id)) {
      throw new Error(`Duplicate admin page ID: ${page.id}`)
    }
    ids.add(page.id)
    if (page.kind === 'builtin' && page.id === 'extension') {
      throw new Error('Builtin page ID is reserved: extension')
    }
    if (!page.title.trim() || !page.path.startsWith('/')) {
      throw new Error(`Invalid admin page definition: ${page.id}`)
    }
    if (page.kind === 'extension' && page.path !== `/extensions/${page.id}`) {
      throw new Error(`Extension page path must match its ID: ${page.id}`)
    }
    if (
      page.kind === 'builtin' &&
      [page.path, ...page.aliases].some(
        (path) => path === '/extensions' || path.startsWith('/extensions/'),
      )
    ) {
      throw new Error(`Builtin page cannot use the extension path namespace: ${page.id}`)
    }
    if (
      page.kind === 'builtin' &&
      [page.path, ...page.aliases].some(
        (path) =>
          RESERVED_BUILTIN_PATHS.has(path) ||
          RESERVED_BUILTIN_PREFIXES.some((prefix) => path.startsWith(prefix)),
      )
    ) {
      throw new Error(`Builtin page cannot use a reserved path: ${page.id}`)
    }
    for (const path of [page.path, ...page.aliases]) {
      if (!path.startsWith('/') || paths.has(path)) {
        throw new Error(`Duplicate or invalid admin page path: ${path}`)
      }
      paths.add(path)
    }
    if (page.navigation && !Number.isSafeInteger(page.navigation.order)) {
      throw new Error(`Invalid navigation order for admin page: ${page.id}`)
    }
  }
  return definitions
}

/** 侧边栏顺序由 order 决定，同序时按 ID 保持稳定。 */
export function sortNavigationPages(pages: AdminPageDefinition[]): NavigationPage[] {
  return pages
    .filter((page): page is NavigationPage => page.navigation !== undefined)
    .sort(
      (left, right) =>
        left.navigation.order - right.navigation.order || left.id.localeCompare(right.id),
    )
}

/** 按扩展 ID 查找页面，未知 ID 返回空值。 */
export function findExtensionPage(
  pages: AdminPageDefinition[],
  extensionId: string,
): AdminPageDefinition | undefined {
  return pages.find((page) => page.kind === 'extension' && page.id === extensionId)
}
