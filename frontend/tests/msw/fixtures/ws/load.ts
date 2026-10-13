import { readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'

const FIXTURES_DIR = import.meta.dirname

/**
 * Reads every recorded `*.json` frame in this directory, keyed by path (`./<frame>.json`) the way
 * Vite's `import.meta.glob` did, so callers can keep deriving the frame type from the file name.
 */
export const loadWsFixtures = (filter: RegExp = /./): Record<string, unknown> =>
  Object.fromEntries(
    readdirSync(FIXTURES_DIR)
      .filter((file) => file.endsWith('.json') && filter.test(file))
      .sort()
      .map((file) => [`./${file}`, JSON.parse(readFileSync(path.join(FIXTURES_DIR, file), 'utf8')) as unknown]),
  )
