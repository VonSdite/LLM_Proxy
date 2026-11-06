import { realpath, rm } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from 'vite'

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const root = await realpath(resolve(frontend, '..'))
const staticDirectory = await realpath(resolve(root, 'src/presentation/static'))
const output = resolve(staticDirectory, 'app')
if (staticDirectory !== resolve(root, 'src/presentation/static')) {
  throw new Error('The frontend output must stay inside the repository static directory.')
}
// 只清理已验证目录中的前端产物；Ace 等共享资源位于其外。
await rm(output, { recursive: true, force: true })
await build({ root: frontend })
