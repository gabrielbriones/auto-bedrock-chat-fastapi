import { describe, expect, it } from 'vitest'

import type { VariableValue } from '@/domains/prompt-catalog/domain/variable-value'

// The type has no runtime behaviour of its own; this just pins the shape so a future edit that
// breaks a discriminant is caught here first instead of in every module that consumes it.
describe('VariableValue', () => {
  it('discriminates on kind', () => {
    const values: readonly VariableValue[] = [
      { kind: 'text', value: 'abc' },
      { kind: 'select', value: 'linux' },
      { kind: 'number', value: 5 },
      { kind: 'boolean', value: true },
    ]

    expect(values.map((value) => value.kind)).toEqual(['text', 'select', 'number', 'boolean'])
  })
})
