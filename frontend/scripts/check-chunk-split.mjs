#!/usr/bin/env node
// FR-SHELL-015: the admin subtree must stay a lazily loaded chunk that the chat entry never
// pulls in statically. Asserted from the build manifest's own import graph, so a route that
// starts being imported eagerly fails the build rather than quietly growing the entry chunk.
// Run via `npm run check:chunks`, after `vite build`.
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const manifestPath = path.resolve(here, '..', 'dist', '.vite', 'manifest.json')

const ADMIN_ROUTE_PREFIX = 'src/routes/dashboard/'

// FIX-03: the legacy client shipped a highlighter on every page load and never called it. Shiki
// must stay behind `CodeBlock`'s dynamic imports, so the entry graph may not reach it.
const SHIKI_CHUNK = /(^|\/)shiki\//

function collectStaticImports(manifest, key, seen = new Set()) {
  if (seen.has(key)) {
    return seen
  }

  seen.add(key)

  for (const imported of manifest[key]?.imports ?? []) {
    collectStaticImports(manifest, imported, seen)
  }

  return seen
}

async function main() {
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'))

  const entryKey = Object.keys(manifest).find((key) => manifest[key].isEntry === true)

  if (entryKey === undefined) {
    console.error(`Chunk split check failed: no entry chunk in ${manifestPath}.`)
    process.exitCode = 1
    return
  }

  const adminKeys = Object.keys(manifest).filter((key) => key.startsWith(ADMIN_ROUTE_PREFIX))

  if (adminKeys.length === 0) {
    console.error('Chunk split check failed: the build emitted no admin route chunk at all.')
    process.exitCode = 1
    return
  }

  const eager = [...collectStaticImports(manifest, entryKey)].filter((key) =>
    key.startsWith(ADMIN_ROUTE_PREFIX),
  )

  if (eager.length > 0) {
    console.error('Chunk split check failed: the chat entry statically imports admin routes:\n')
    for (const key of eager) {
      console.error(`  - ${key}`)
    }
    process.exitCode = 1
    return
  }

  const notSplit = adminKeys.filter((key) => manifest[key].isDynamicEntry !== true)

  if (notSplit.length > 0) {
    console.error('Chunk split check failed: admin routes are not separate chunks:\n')
    for (const key of notSplit) {
      console.error(`  - ${key}`)
    }
    process.exitCode = 1
    return
  }

  const adminGuardErrorChunk = adminKeys.find((key) =>
    key.startsWith('src/routes/dashboard/route.tsx?tsr-split=errorComponent'),
  )

  if (adminGuardErrorChunk !== undefined) {
    console.error(
      'Chunk split check failed: access denial is inside an admin error chunk, so denied users would fetch it.',
    )
    process.exitCode = 1
    return
  }

  const shikiKeys = Object.keys(manifest).filter((key) => SHIKI_CHUNK.test(key))

  if (shikiKeys.length === 0) {
    console.error('Chunk split check failed: the build emitted no shiki chunk, so this check is vacuous.')
    process.exitCode = 1
    return
  }

  const eagerShiki = [...collectStaticImports(manifest, entryKey)].filter((key) =>
    SHIKI_CHUNK.test(key),
  )

  if (eagerShiki.length > 0) {
    console.error('Chunk split check failed: the chat entry statically imports shiki (FIX-03):\n')
    for (const key of eagerShiki) {
      console.error(`  - ${key}`)
    }
    process.exitCode = 1
    return
  }

  console.log(
    `Chunk split check passed (${adminKeys.length} lazy admin chunk(s), ${shikiKeys.length} lazy shiki chunk(s)).`,
  )
}

await main()
