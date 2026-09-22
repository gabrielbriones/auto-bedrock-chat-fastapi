#!/usr/bin/env node
// STD-002 §6 "Copy diff": every quoted legacy string in LEGACY_STRINGS must exist verbatim
// (modulo `{placeholder}` segments) somewhere under src/shared/copy/. Run via `npm run check:copy`.
import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(here, '..')
const copyDir = path.join(repoRoot, 'src', 'shared', 'copy')

async function readCopySource() {
  const entries = await readdir(copyDir, { recursive: true, withFileTypes: true })
  const files = entries.filter((entry) => entry.isFile() && entry.name.endsWith('.ts'))

  const contents = await Promise.all(
    files.map((entry) => readFile(path.join(entry.parentPath ?? entry.path, entry.name), 'utf8')),
  )

  return contents.join('\n')
}

function toPattern(legacyString) {
  const escaped = legacyString.replace(/[.*+?^${}()|[\]\\]/g, (char) =>
    char === '{' || char === '}' ? char : `\\${char}`,
  )
  const withWildcards = escaped.replace(/\{[^}]*\}/g, '[^"`]+')
  return new RegExp(withWildcards)
}

async function main() {
  const { default: LEGACY_STRINGS } = await import(path.join(copyDir, 'legacy-strings.json'), {
    with: { type: 'json' },
  })
  const source = await readCopySource()

  const missing = LEGACY_STRINGS.filter((legacyString) => !toPattern(legacyString).test(source))

  if (missing.length > 0) {
    console.error('Copy parity check failed. Missing verbatim from src/shared/copy/:\n')
    for (const legacyString of missing) {
      console.error(`  - ${legacyString}`)
    }
    process.exitCode = 1
    return
  }

  console.log(`Copy parity check passed (${LEGACY_STRINGS.length} legacy string(s) verified).`)
}

await main()
