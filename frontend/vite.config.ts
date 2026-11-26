import { resolve } from 'node:path'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'

const projectRoot = fileURLToPath(new URL('..', import.meta.url))

/** 监听仓库根扩展目录，使新增、删除和页面描述修改进入开发服务。 */
function adminExtensionPlugin(): Plugin {
  const extensionsRoot = resolve(projectRoot, 'extensions').replaceAll('\\', '/')
  return {
    name: 'llm-proxy-admin-extensions',
    configureServer(server) {
      server.watcher.add(extensionsRoot)
      const refresh = (file: string) => {
        const normalized = file.replaceAll('\\', '/')
        if (!normalized.startsWith(`${extensionsRoot}/`)) return
        if (!normalized.endsWith('/frontend/index.ts')) return
        server.environments.client.moduleGraph.invalidateAll()
        server.ws.send({ type: 'full-reload' })
      }
      server.watcher.on('add', refresh)
      server.watcher.on('unlink', refresh)
      server.watcher.on('change', refresh)
    },
  }
}

export default defineConfig(({ command }) => ({
  plugins: [vue(), adminExtensionPlugin()],
  base: command === 'build' ? '/static/app/' : '/',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      'vue-router': fileURLToPath(new URL('./node_modules/vue-router', import.meta.url)),
      pinia: fileURLToPath(new URL('./node_modules/pinia', import.meta.url)),
      vue: fileURLToPath(new URL('./node_modules/vue', import.meta.url)),
    },
  },
  build: {
    outDir: '../src/presentation/static/app',
    emptyOutDir: false,
    manifest: 'manifest.json',
    sourcemap: false,
    // 通过独立映射引用分块，避免依赖文件的哈希变化向其他页面传播。
    chunkImportMap: true,
    // 分块标识统一相对前端目录，保持不同命令调用目录下的产物一致。
    rolldownOptions: { cwd: fileURLToPath(new URL('.', import.meta.url)) },
  },
  server: {
    fs: { allow: [projectRoot] },
    port: 5173,
    strictPort: true,
    watch: {
      ignored: ['**/playwright-report/**', '**/test-results/**', '**/*-snapshots/**'],
    },
    proxy: {
      '^/api(?:/|$)': {
        target: process.env.LLM_PROXY_DEV_URL || 'http://127.0.0.1:8080',
        changeOrigin: false,
      },
      '/static/vendor': { target: process.env.LLM_PROXY_DEV_URL || 'http://127.0.0.1:8080' },
      '/logout': { target: process.env.LLM_PROXY_DEV_URL || 'http://127.0.0.1:8080' },
    },
  },
}))
