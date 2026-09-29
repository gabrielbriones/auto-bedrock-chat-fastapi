import { describe, expect, it } from '@jest/globals'

import { isOverrideKey, OVERRIDE_KEYS } from '@/domains/model-config/domain/override-key'

describe('isOverrideKey', () => {
  it('accepts every key in OVERRIDE_KEYS and rejects an unknown key', () => {
    for (const key of OVERRIDE_KEYS) {
      expect(isOverrideKey(key)).toBe(true)
    }
    expect(isOverrideKey('not_a_real_key')).toBe(false)
  })
})
