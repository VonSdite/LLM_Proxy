import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import type { Pinia } from 'pinia'
import { usePageRegistry } from '@/stores/pages'
import { useSession } from '@/stores/session'
import { adminPageRouteName, EXTENSION_ROUTE_NAME } from './page-contract'

export function createAppRouter(pinia: Pinia) {
  const pages = usePageRegistry(pinia)
  const builtinRoutes: RouteRecordRaw[] = pages.pages
    .filter((page) => page.kind === 'builtin')
    .map((page) => ({
      path: page.path,
      ...(page.aliases.length ? { alias: [...page.aliases] } : {}),
      name: adminPageRouteName(page),
      component: page.component,
      meta: { title: page.title },
    }))
  const router = createRouter({
    history: createWebHistory(),
    routes: [
      ...builtinRoutes,
      {
        path: '/extensions/:pageId',
        name: EXTENSION_ROUTE_NAME,
        component: () => import('@/app/ExtensionPageHost.vue'),
        meta: { title: '扩展页面' },
      },
      {
        path: '/login',
        component: () => import('@/pages/LoginPage.vue'),
        meta: { title: '登录', public: true },
      },
      ...(import.meta.env.DEV || import.meta.env.MODE === 'test'
        ? [
            {
              path: '/dev/components',
              component: () => import('@/dev/ComponentsPage.vue'),
              meta: { title: '组件展示' },
            },
          ]
        : []),
    ],
    scrollBehavior: (_to, _from, saved) => saved || { top: 0 },
  })
  router.beforeEach(async (to) => {
    const session = useSession(pinia)
    if (!session.context) {
      try {
        await session.refresh()
      } catch {
        return true
      }
    }
    const context = session.context
    if (!context?.authenticated && !to.meta.public) return '/login'
    if (to.path === '/login' && context?.authenticated) {
      return '/'
    }
    if (to.name === EXTENSION_ROUTE_NAME) {
      const extensionId = typeof to.params.pageId === 'string' ? to.params.pageId : ''
      to.meta.title = pages.extensionPage(extensionId)?.title || '扩展页面'
    }
    return true
  })
  router.afterEach((to) => {
    document.title = `${String(to.meta.title || '管理控制台')} · LLM Proxy`
  })
  return router
}
