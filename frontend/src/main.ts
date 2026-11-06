import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './app/App.vue'
import { createAppRouter } from './app/router'
import { onUnauthorized } from './api/client'
import { useSession } from './stores/session'
import './styles/base.css'

const pinia = createPinia()
const router = createAppRouter(pinia)
onUnauthorized(() => {
  useSession(pinia).clear()
  if (router.currentRoute.value.path !== '/login') void router.replace('/login')
})
createApp(App).use(pinia).use(router).mount('#app')
