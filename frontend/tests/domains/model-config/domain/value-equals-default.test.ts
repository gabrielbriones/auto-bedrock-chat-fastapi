import { describe, expect, it } from '@jest/globals'

import { valueEqualsDefault } from '@/domains/model-config/domain/value-equals-default'

describe('valueEqualsDefault', () => {
  it('coerces booleans (Q10)', () => {
    expect(valueEqualsDefault(true, true)).toBe(true)
    expect(valueEqualsDefault(false, true)).toBe(false)
    expect(valueEqualsDefault(1, true)).toBe(true)
    expect(valueEqualsDefault(0, true)).toBe(false)
  })

  it('tolerates float error within 1e-9', () => {
    expect(valueEqualsDefault(0.7000000001, 0.7)).toBe(true)
  })

  it('treats a float difference beyond the tolerance as different', () => {
    expect(valueEqualsDefault(0.701, 0.7)).toBe(false)
  })

  it('falls back to strict equality for strings', () => {
    expect(valueEqualsDefault('claude', 'claude')).toBe(true)
    expect(valueEqualsDefault('claude', 'gpt')).toBe(false)
  })
})
