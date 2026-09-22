import { describe, expect, it } from 'vitest'

import { OVERRIDE_FIELDS, overrideFieldFor } from '@/domains/model-config/domain/override-field'
import { OVERRIDE_KEYS } from '@/domains/model-config/domain/override-key'

describe('OVERRIDE_FIELDS', () => {
  it('declares every OverrideKey exactly once', () => {
    const keys = OVERRIDE_FIELDS.map((field) => field.key)
    expect(keys).toEqual(OVERRIDE_KEYS)
  })

  it('marks temperature and top_p as requiring temperature support', () => {
    expect(overrideFieldFor('temperature').requiresTemperatureSupport).toBe(true)
    expect(overrideFieldFor('top_p').requiresTemperatureSupport).toBe(true)
    expect(overrideFieldFor('max_tokens').requiresTemperatureSupport).toBeUndefined()
  })

  it('makes the KB fields depend on enable_rag being on', () => {
    expect(overrideFieldFor('kb_top_k_results').dependsOn).toEqual({ key: 'enable_rag', requiredValue: true })
    expect(overrideFieldFor('kb_similarity_threshold').dependsOn).toEqual({ key: 'enable_rag', requiredValue: true })
  })
})

describe('overrideFieldFor', () => {
  it('throws for a key outside OVERRIDE_FIELDS', () => {
    // @ts-expect-error deliberately invalid input to exercise the guard
    expect(() => overrideFieldFor('not_a_real_key')).toThrow()
  })
})
