import { createRouter, createWebHistory } from 'vue-router'
import type { Pinia } from 'pinia'
import { useSession } from '@/stores/session'

export function createAppRouter(pinia: Pinia) {
  const router = createRouter({
    history: createWebHistory(),
    routes: [
      {
        path: '/statistics',
        component: () => import('@/pages/StatisticsPage.vue'),
        meta: { title: '统计与日志' },
      },
      {
        path: '/oauth',
        component: () => import('@/pages/OAuthPage.vue'),
        meta: { title: 'Codex OAuth', feature: 'oauth' },
      },
      {
        path: '/',
        alias: '/providers',
        component: () => import('@/pages/ProvidersPage.vue'),
        meta: { title: 'Provider' },
      },
      {
        path: '/model-mappings',
        component: () => import('@/pages/ModelMappingsPage.vue'),
        meta: { title: '模型映射', feature: 'model_mapping' },
      },
      { path: '/users', component: () => import('@/pages/UsersPage.vue'), meta: { title: '用户' } },
      {
        path: '/api-keys',
        component: () => import('@/pages/ApiKeysPage.vue'),
        meta: { title: 'API Key', feature: 'api_keys' },
      },
      {
        path: '/agents',
        component: () => import('@/pages/AgentsPage.vue'),
        meta: { title: 'Agent 配置' },
      },
      {
        path: '/login',
        component: () => import('@/pages/LoginPage.vue'),
        meta: { title: '登录', public: true },
      },
      {
        path: '/settings',
        component: () => import('@/pages/SettingsPage.vue'),
        meta: { title: '系统设置' },
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
    const gate = to.meta.feature as 'oauth' | 'api_keys' | 'model_mapping' | undefined
    if (gate && !context?.features[gate]) return '/settings'
    return true
  })
  router.afterEach((to) => {
    document.title = `${String(to.meta.title || '管理控制台')} · LLM Proxy`
  })
  return router
}
