import { readFile } from 'node:fs/promises'

type ManifestChunk = {
  readonly file: string
  readonly isEntry?: boolean
  readonly imports?: readonly string[]
  readonly dynamicImports?: readonly string[]
}

type Manifest = Record<string, ManifestChunk>

const ADMIN_ROUTE_PREFIX = 'src/routes/dashboard/'
const ROUTE_PREFIX = 'src/routes/'

// Mirrors scripts/check-chunk-split.mjs's SHIKI_CHUNK (FIX-03): shiki must stay behind CodeBlock's
// dynamic imports, same as the admin subtree (FR-SHELL-015).
const SHIKI_CHUNK = /(^|\/)shiki\//

const collect = (
  manifest: Manifest,
  seedKeys: readonly string[],
  follow: (chunk: ManifestChunk) => readonly string[],
): Set<string> => {
  const seen = new Set<string>()
  const queue = [...seedKeys]

  for (let key = queue.shift(); key !== undefined; key = queue.shift()) {
    if (seen.has(key)) {
      continue
    }

    const chunk = manifest[key]

    if (chunk === undefined) {
      continue
    }

    seen.add(key)
    queue.push(...follow(chunk))
  }

  return seen
}

// Both static `imports` and lazy `dynamicImports`: a route's own closure includes whatever it
// dynamically pulls in once rendered (e.g. the chat route reaching `sanitized-markdown`, or an
// admin route reaching recharts), not just what the bundler wires in eagerly.
const fullClosure = (manifest: Manifest, seedKeys: readonly string[]): Set<string> =>
  collect(manifest, seedKeys, (chunk) => [...(chunk.imports ?? []), ...(chunk.dynamicImports ?? [])])

// The entry's *own* dynamicImports are the router's registration of every route file — admin
// included — as an `import()` call site it merely knows about, not one it invokes on a chat
// session. Only its static `imports` are unconditionally loaded (mirrors
// scripts/check-chunk-split.mjs's collectStaticImports).
const staticClosure = (manifest: Manifest, seedKeys: readonly string[]): Set<string> =>
  collect(manifest, seedKeys, (chunk) => chunk.imports ?? [])

export type ChatRouteBundleAnalysis = {
  /** Files the chat entry's static import graph always loads, on every route. */
  readonly entryFiles: ReadonlySet<string>
  /** Files only an admin route (or shiki) can reach — must never load on the chat route. */
  readonly forbiddenFiles: ReadonlySet<string>
}

// NFR-PERF-007: derives, from the build's own manifest, every file that a chat-only session must
// never fetch. A file only counts as forbidden if the chat routes' own full closure (their own
// imports *and* dynamic imports — so shared lazy chunks like `sanitized-markdown` or a UI
// primitive don't get mistaken for admin-only) never reaches it either, so only genuinely
// admin/shiki-exclusive chunks — like recharts's `BarChart-*.js`, reachable only transitively from
// admin usage/stats routes — end up forbidden.
export const analyzeChatRouteBundle = async (manifestPath: string): Promise<ChatRouteBundleAnalysis> => {
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8')) as Manifest

  const entryKey = Object.keys(manifest).find((key) => manifest[key]?.isEntry === true)

  if (entryKey === undefined) {
    throw new Error(`no entry chunk found in ${manifestPath}`)
  }

  const entryFiles = new Set([...staticClosure(manifest, [entryKey])].map((key) => manifest[key]!.file))

  const adminKeys = Object.keys(manifest).filter((key) => key.startsWith(ADMIN_ROUTE_PREFIX))
  const shikiKeys = Object.keys(manifest).filter((key) => SHIKI_CHUNK.test(key))
  const chatRouteKeys = Object.keys(manifest).filter(
    (key) => key.startsWith(ROUTE_PREFIX) && !key.startsWith(ADMIN_ROUTE_PREFIX),
  )

  if (adminKeys.length === 0 || shikiKeys.length === 0) {
    throw new Error(
      'bundle separation check is vacuous: the build emitted no admin route or shiki chunk to forbid',
    )
  }

  if (chatRouteKeys.length === 0) {
    throw new Error('bundle separation check is vacuous: the build emitted no non-admin route chunk')
  }

  // Seeded from the entry's static graph plus each chat route's *own* closure — never from the
  // entry's dynamicImports, which is the router's registry of every route and would otherwise
  // make this vacuous by "reaching" admin too.
  const chatClosureKeys = new Set([
    ...staticClosure(manifest, [entryKey]),
    ...fullClosure(manifest, chatRouteKeys),
  ])
  const adminClosureKeys = fullClosure(manifest, [...adminKeys, ...shikiKeys])

  const forbiddenFiles = new Set(
    [...adminClosureKeys]
      .filter((key) => !chatClosureKeys.has(key))
      .map((key) => manifest[key]!.file),
  )

  if (forbiddenFiles.size === 0) {
    throw new Error('bundle separation check is vacuous: no forbidden files were derived from the manifest')
  }

  return { entryFiles, forbiddenFiles }
}
