import { execFile } from 'node:child_process'
import { mkdtemp, mkdir, readdir, rm, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { promisify } from 'node:util'
import { describe, expect, it } from 'vitest'

const execFileAsync = promisify(execFile)

const frontend = process.cwd()
const projectRoot = resolve(frontend, '..')

describe('外部扩展构建', () => {
  it('使用生产配置解析 Vue、Pinia、Vue Router 和宿主页面协议', async () => {
    const temporaryRoot = resolve(projectRoot, 'tmp')
    await mkdir(temporaryRoot, { recursive: true })
    const fixture = await mkdtemp(resolve(temporaryRoot, 'extension-build-'))
    try {
      await writeFile(
        resolve(fixture, 'index.ts'),
        `import { defineStore } from 'pinia'
import { useRoute } from 'vue-router'
import { defineExtensionPage } from '@/app/page-contract'

export const useExternalStore = defineStore('external-fixture', () => ({ ready: true }))
export { useRoute }
export default defineExtensionPage({
  id: 'external-fixture',
  title: 'External fixture',
  component: () => import('./ExternalPage.vue'),
})
`,
        'utf8',
      )
      await writeFile(
        resolve(fixture, 'ExternalPage.vue'),
        `<script setup lang="ts">
import { ref } from 'vue'
const ready = ref(true)
</script>
<template><p>{{ ready }}</p></template>
`,
        'utf8',
      )

      const buildScript = `import { build } from 'vite'
const [configFile, root] = process.argv.slice(1)
await build({
  configFile,
  root,
  logLevel: 'silent',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    manifest: false,
    lib: { entry: root + '/index.ts', formats: ['es'], fileName: 'extension' },
  },
})
`
      await execFileAsync(
        process.execPath,
        [
          '--input-type=module',
          '--eval',
          buildScript,
          resolve(frontend, 'vite.config.ts'),
          fixture,
        ],
        {
          cwd: frontend,
          env: { ...process.env, TMP: temporaryRoot, TEMP: temporaryRoot, TMPDIR: temporaryRoot },
        },
      )
      const outputFiles = await readdir(resolve(fixture, 'dist'))
      expect(outputFiles.some((file) => /^extension\.m?js$/.test(file))).toBe(true)
    } finally {
      await rm(fixture, { recursive: true, force: true })
    }
  }, 30_000)
})
