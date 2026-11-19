import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      'vue-router': fileURLToPath(new URL('./node_modules/vue-router', import.meta.url)),
      pinia: fileURLToPath(new URL('./node_modules/pinia', import.meta.url)),
      vue: fileURLToPath(new URL('./node_modules/vue', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    include: ['tests/unit/**/*.test.ts'],
    setupFiles: ['tests/unit/setup.ts'],
    restoreMocks: true,
    pool: 'vmThreads',
  },
})
