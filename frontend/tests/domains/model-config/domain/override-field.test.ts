import { describe, expect, it } from '@jest/globals'

import { OVERRIDE_FIELDS, overrideFieldFor } from '@/domains/model-config/domain/override-field'
import { OVERRIDE_KEYS } from '@/domains/model-config/domain/override-key'

describe('OVERRIDE_FIELDS', () => {
  it('declares every OverrideKey exactly once', () => {
    const keys = OVERRIDE_FIELDS.map((field) => field.key)
    expect(keys).toEqual(OVERRIDE_KEYS)
  })
})

describe('overrideFieldFor', () => {
  it('throws for a key outside OVERRIDE_FIELDS', () => {
    // @ts-expect-error deliberately invalid input to exercise the guard
    expect(() => overrideFieldFor('not_a_real_key')).toThrow()
  })
})
