import path from 'node:path'

import { ESLint } from 'eslint'
import { beforeAll, describe, expect, it } from 'vitest'

// FR-TOOL-010 and FR-TOOL-011 are configuration over a published plugin, and a misconfigured rule
// reports nothing at all — which is indistinguishable from a clean tree. This lints one module of
// deliberate violations with the repo's real configuration, so a rule that stops matching fails
// here instead of passing silently. `ignore: false` is what reaches past the global ignore.
const VIOLATIONS = 'tests/domains/_lint-check/domain/violations.ts'

let messages: ReadonlyArray<string>

beforeAll(async () => {
  const cwd = path.join(import.meta.dirname, '..', '..')
  const results = await new ESLint({ cwd, ignore: false }).lintFiles([VIOLATIONS])

  messages = (results[0]?.messages ?? [])
    .filter((message) => message.ruleId === 'boundaries/dependencies')
    .map((message) => message.message)
}, 30_000)

describe('the architecture rules still fire', () => {
  it('rejects a framework import in the domain layer (FR-TOOL-010)', () => {
    expect(messages).toContainEqual(expect.stringContaining('FR-TOOL-010'))
  })

  it('rejects reaching past another context’s domain/public.ts (FR-TOOL-011)', () => {
    expect(messages).toContainEqual(expect.stringContaining('FR-TOOL-011'))
  })

  it('accepts the public API of another context, so the rule is not banning everything', () => {
    expect(messages).toHaveLength(2)
  })
})
