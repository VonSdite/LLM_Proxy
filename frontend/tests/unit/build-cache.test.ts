import { execFile } from 'node:child_process'
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { promisify } from 'node:util'
import { describe, expect, it } from 'vitest'

const execFileAsync = promisify(execFile)
const frontend = process.cwd()

describe('生产构建资源缓存', () => {
  it('页面内容和样式变化时保留入口及其他页面的产物', async () => {
    const temporaryRoot = resolve(frontend, '../tmp')
    await mkdir(temporaryRoot, { recursive: true })
    const fixture = await mkdtemp(resolve(temporaryRoot, 'build-cache-'))
    try {
      await writeFile(
        resolve(fixture, 'index.html'),
        '<script type="module" src="/main.js"></script>',
      )
      // 入口导出共享内容，异步页面再引用入口，覆盖哈希沿循环引用传播的场景。
      await writeFile(
        resolve(fixture, 'main.js'),
        `export const shared = 'shared'
window.loadPage = () => import('./page.js')
window.loadOther = () => import('./other.js')
`,
      )
      const page = `import { shared } from './main.js'
import './page.css'
export const content = shared + 'before'
`
      await writeFile(resolve(fixture, 'page.js'), page)
      await writeFile(resolve(fixture, 'page.css'), '.page { color: red; }')
      await writeFile(
        resolve(fixture, 'other.js'),
        "import { shared } from './main.js'; export const content = shared + 'other'",
      )

      const buildScript = `import { build } from 'vite'
const [configFile, root, cwd] = process.argv.slice(1)
process.chdir(cwd)
await build({
  configFile,
  configLoader: 'runner',
  root,
  logLevel: 'silent',
  build: { outDir: 'dist', emptyOutDir: true },
})
`
      const build = async (cwd = frontend) => {
        await execFileAsync(
          process.execPath,
          [
            '--input-type=module',
            '--eval',
            buildScript,
            resolve(frontend, 'vite.config.ts'),
            fixture,
            cwd,
          ],
          {
            cwd: frontend,
            env: { ...process.env, TMP: temporaryRoot, TEMP: temporaryRoot, TMPDIR: temporaryRoot },
          },
        )
        const manifest = JSON.parse(await readFile(resolve(fixture, 'dist/manifest.json'), 'utf8'))
        const importMap = JSON.parse(
          await readFile(resolve(fixture, 'dist/importmap.json'), 'utf8'),
        )
        const entry = await readFile(resolve(fixture, 'dist', manifest['index.html'].file), 'utf8')
        const other = await readFile(resolve(fixture, 'dist', manifest['other.js'].file), 'utf8')
        return { manifest, importMap, entry, other }
      }

      const before = await build()
      const fromProjectRoot = await build(resolve(frontend, '..'))
      expect(fromProjectRoot).toEqual(before)
      await writeFile(resolve(fixture, 'page.js'), page.replace('before', 'after'))
      await writeFile(resolve(fixture, 'page.css'), '.page { color: blue; }')
      const after = await build()

      expect(after.manifest['page.js'].file).not.toBe(before.manifest['page.js'].file)
      expect(after.manifest['page.js'].css).not.toEqual(before.manifest['page.js'].css)
      expect(after.manifest['index.html'].file).toBe(before.manifest['index.html'].file)
      expect(after.manifest['other.js'].file).toBe(before.manifest['other.js'].file)
      expect(after.entry).toBe(before.entry)
      expect(after.other).toBe(before.other)
      expect(Object.keys(after.importMap.imports)).toEqual(Object.keys(before.importMap.imports))
      expect(after.importMap).not.toEqual(before.importMap)
    } finally {
      await rm(fixture, { recursive: true, force: true })
    }
  }, 30_000)
})
