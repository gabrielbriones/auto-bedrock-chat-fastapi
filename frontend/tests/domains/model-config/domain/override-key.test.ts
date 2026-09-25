import { describe, expect, it } from '@jest/globals'

import { isOverrideKey, OVERRIDE_KEYS } from '@/domains/model-config/domain/override-key'

describe('isOverrideKey', () => {
  it('accepts every key in OVERRIDE_KEYS', () => {
    for (const key of OVERRIDE_KEYS) {
      expect(isOverrideKey(key)).toBe(true)
    }
  })

  it('rejects an unknown key', () => {
    expect(isOverrideKey('not_a_real_key')).toBe(false)
  })
})
