import { describe, expect, it } from 'vitest'
import type { Component } from 'vue'
import {
  defineBuiltinPage,
  defineExtensionPage,
  type AdminPageDefinition,
} from '@/app/page-contract'
import { builtinPages } from '@/app/builtin-pages'
import {
  findExtensionPage,
  sortNavigationPages,
  validatePageDefinitions,
} from '@/app/page-registry'

const component = () => Promise.resolve({ default: { name: 'FixturePage' } as Component })

function extensionPage(id: string, order = 900): AdminPageDefinition {
  return defineExtensionPage({
    id,
    title: `扩展 ${id}`,
    component,
    navigation: { label: id, icon: 'terminal', order },
  })
}

describe('admin page registry', () => {
  it('keeps builtin routes, titles, aliases and navigation order', () => {
    const pages = validatePageDefinitions(builtinPages)
    expect(pages.map((page) => page.path)).toEqual([
      '/',
      '/oauth',
      '/model-mappings',
      '/api-keys',
      '/agents',
      '/users',
      '/statistics',
      '/settings',
    ])
    expect(pages[0]?.aliases).toEqual(['/providers'])
    expect(sortNavigationPages(pages).map((page) => page.navigation.label)).toEqual([
      'Provider',
      'OAuth',
      '模型映射',
      'API Key',
      'Agent 配置',
      '用户',
      '统计',
      '设置',
    ])
  })

  it('derives extension routes and inserts them by order and ID', () => {
    const page = extensionPage('request-tools', 650)
    expect(page.path).toBe('/extensions/request-tools')
    expect(page.kind).toBe('extension')
    const pages = validatePageDefinitions([
      extensionPage('zeta', 650),
      ...builtinPages,
      extensionPage('alpha', 650),
      page,
    ])
    const labels = sortNavigationPages(pages).map((item) => item.navigation.label)
    expect(labels.slice(5, 9)).toEqual(['用户', 'alpha', 'request-tools', 'zeta'])
    expect(findExtensionPage(pages, 'request-tools')).toBe(page)
    expect(findExtensionPage(pages, 'missing')).toBeUndefined()
  })

  it('rejects duplicate IDs and paths', () => {
    const page = extensionPage('duplicate-page')
    expect(() => validatePageDefinitions([page, page])).toThrow('Duplicate admin page ID')
    expect(() =>
      validatePageDefinitions([
        ...builtinPages,
        defineBuiltinPage({
          id: 'custom-settings',
          path: '/settings',
          title: '自定义设置',
          component,
        }),
      ]),
    ).toThrow('Duplicate or invalid admin page path')
  })

  it('protects the extension path namespace', () => {
    expect(() =>
      validatePageDefinitions([
        defineBuiltinPage({
          id: 'invalid-extension',
          path: '/invalid-extension',
          aliases: ['/extensions/invalid-extension'],
          title: '无效扩展',
          component,
        }),
      ]),
    ).toThrow('Builtin page cannot use the extension path namespace')
    expect(() =>
      validatePageDefinitions([{ ...extensionPage('wrong-path'), path: '/settings' }]),
    ).toThrow('Extension page path must match its ID')
  })

  it('reserves the extension host route name and core paths', () => {
    expect(() =>
      validatePageDefinitions([
        defineBuiltinPage({
          id: 'extension',
          path: '/reserved-host',
          title: '保留路由',
          component,
        }),
      ]),
    ).toThrow('Builtin page ID is reserved')
    expect(() =>
      validatePageDefinitions([
        defineBuiltinPage({
          id: 'invalid-login',
          path: '/login',
          title: '无效登录页',
          component,
        }),
      ]),
    ).toThrow('Builtin page cannot use a reserved path')
  })
})
