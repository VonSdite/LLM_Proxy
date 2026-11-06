import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ command }) => ({
  plugins: [vue()],
  base: command === 'build' ? '/static/app/' : '/',
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: {
    outDir: '../src/presentation/static/app',
    emptyOutDir: false,
    manifest: 'manifest.json',
    sourcemap: false,
  },
  server: {
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
