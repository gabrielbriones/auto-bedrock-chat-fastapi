import { describe, expect, it } from '@jest/globals'

import { buildModelCatalog } from '@/domains/model-config/domain/model-catalog'
import { toConfigurationProfile } from '@/domains/model-config/domain/configuration-profile'
import { overriddenKeys, overrideCount } from '@/domains/model-config/domain/override-delta'

const catalog = buildModelCatalog([], [])

describe('overriddenKeys / overrideCount', () => {
  it('is empty when there are no overrides at all', () => {
    const profile = toConfigurationProfile({ temperature: 0.7 }, null, catalog)
    expect(overriddenKeys(profile)).toEqual([])
    expect(overrideCount(profile)).toBe(0)
  })

  it('counts an override that differs from its default', () => {
    const profile = { ...toConfigurationProfile({ temperature: 0.7 }, null, catalog), overrides: { temperature: 0.2 } }
    expect(overriddenKeys(profile)).toEqual(['temperature'])
    expect(overrideCount(profile)).toBe(1)
  })

  it('moved and moved back counts as zero (FR-CFG-006 Q10)', () => {
    const profile = { ...toConfigurationProfile({ temperature: 0.7 }, null, catalog), overrides: { temperature: 0.7 } }
    expect(overrideCount(profile)).toBe(0)
  })

  it('a float within tolerance of the default is not counted', () => {
    const profile = {
      ...toConfigurationProfile({ temperature: 0.7 }, null, catalog),
      overrides: { temperature: 0.7000000001 },
    }
    expect(overrideCount(profile)).toBe(0)
  })

  it('counts an override with no known default', () => {
    const profile = { ...toConfigurationProfile({}, null, catalog), overrides: { temperature: 0.2 } }
    expect(overrideCount(profile)).toBe(1)
  })
})
