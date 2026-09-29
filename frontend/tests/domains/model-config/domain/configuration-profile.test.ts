import { describe, expect, it } from '@jest/globals'

import { buildModelCatalog } from '@/domains/model-config/domain/model-catalog'
import { toConfigurationProfile, effective, isAllowed } from '@/domains/model-config/domain/configuration-profile'

const catalog = buildModelCatalog([], [])

describe('toConfigurationProfile', () => {
  it('keeps only known keys with a valid value type from overrideDefaults', () => {
    const profile = toConfigurationProfile(
      { temperature: 0.7, unknown_key: 'x', max_tokens: { nested: true } },
      null,
      catalog,
    )

    expect(profile.defaults).toEqual({ temperature: 0.7 })
  })

  it('starts with no overrides (server-confirmed only, I3)', () => {
    const profile = toConfigurationProfile({}, null, catalog)
    expect(profile.overrides).toEqual({})
  })

  it('drops unknown keys from allowedDynamicOverrides but keeps null as "all"', () => {
    expect(toConfigurationProfile({}, null, catalog).allowedKeys).toBeNull()
    expect(toConfigurationProfile({}, ['temperature', 'bogus'], catalog).allowedKeys).toEqual(['temperature'])
  })
})

describe('effective', () => {
  it('prefers the override over the default', () => {
    const profile = { ...toConfigurationProfile({ temperature: 0.7 }, null, catalog), overrides: { temperature: 0.2 } }
    expect(effective(profile, 'temperature')).toBe(0.2)
  })

  it('falls back to the default when there is no override', () => {
    const profile = toConfigurationProfile({ temperature: 0.7 }, null, catalog)
    expect(effective(profile, 'temperature')).toBe(0.7)
  })

  it('is undefined when neither is set', () => {
    const profile = toConfigurationProfile({}, null, catalog)
    expect(effective(profile, 'temperature')).toBeUndefined()
  })
})

describe('isAllowed', () => {
  it('allows every key when allowedKeys is null', () => {
    const profile = toConfigurationProfile({}, null, catalog)
    expect(isAllowed(profile, 'temperature')).toBe(true)
  })

  it('allows only the listed keys otherwise', () => {
    const profile = toConfigurationProfile({}, ['temperature'], catalog)
    expect(isAllowed(profile, 'temperature')).toBe(true)
    expect(isAllowed(profile, 'top_p')).toBe(false)
  })
})
