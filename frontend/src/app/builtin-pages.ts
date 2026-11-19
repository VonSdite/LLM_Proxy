import { defineBuiltinPage, type AdminPageDefinition } from './page-contract'

/** 内置页面与侧边栏共用同一份注册信息。 */
export const builtinPages: AdminPageDefinition[] = [
  defineBuiltinPage({
    id: 'providers',
    path: '/',
    aliases: ['/providers'],
    title: 'Provider',
    component: () => import('@/pages/ProvidersPage.vue'),
    navigation: { label: 'Provider', icon: 'server', order: 100 },
  }),
  defineBuiltinPage({
    id: 'oauth',
    path: '/oauth',
    title: 'Codex OAuth',
    component: () => import('@/pages/OAuthPage.vue'),
    navigation: { label: 'OAuth', icon: 'shield', order: 200 },
  }),
  defineBuiltinPage({
    id: 'model-mappings',
    path: '/model-mappings',
    title: '模型映射',
    component: () => import('@/pages/ModelMappingsPage.vue'),
    navigation: { label: '模型映射', icon: 'network', order: 300 },
  }),
  defineBuiltinPage({
    id: 'api-keys',
    path: '/api-keys',
    title: 'API Key',
    component: () => import('@/pages/ApiKeysPage.vue'),
    navigation: { label: 'API Key', icon: 'key', order: 400 },
  }),
  defineBuiltinPage({
    id: 'agents',
    path: '/agents',
    title: 'Agent 配置',
    component: () => import('@/pages/AgentsPage.vue'),
    navigation: { label: 'Agent 配置', icon: 'terminal', order: 500 },
  }),
  defineBuiltinPage({
    id: 'users',
    path: '/users',
    title: '用户',
    component: () => import('@/pages/UsersPage.vue'),
    navigation: { label: '用户', icon: 'users', order: 600 },
  }),
  defineBuiltinPage({
    id: 'statistics',
    path: '/statistics',
    title: '统计与日志',
    component: () => import('@/pages/StatisticsPage.vue'),
    navigation: { label: '统计', icon: 'chart', order: 700 },
  }),
  defineBuiltinPage({
    id: 'settings',
    path: '/settings',
    title: '系统设置',
    component: () => import('@/pages/SettingsPage.vue'),
    navigation: { label: '设置', icon: 'settings', order: 800 },
  }),
]
