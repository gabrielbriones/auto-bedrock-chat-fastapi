import { describe, expect, it } from '@jest/globals'

import { buildModelCatalog } from '@/domains/model-config/domain/model-catalog'
import { toConfigurationProfile } from '@/domains/model-config/domain/configuration-profile'
import { effectiveModelId, resolveEffectiveModel, resolveModel } from '@/domains/model-config/domain/effective-model'
import type { ModelDescriptor } from '@/domains/model-config/domain/model-descriptor'

const claude: ModelDescriptor = {
  id: 'claude',
  name: 'Claude',
  provider: 'anthropic',
  supportsTemperature: true,
  maxOutputTokens: 4096,
}
const catalog = buildModelCatalog([claude], [])

describe('effectiveModelId', () => {
  it('reads the override when present', () => {
    const profile = { ...toConfigurationProfile({ model_id: 'default' }, null, catalog), overrides: { model_id: 'claude' } }
    expect(effectiveModelId(profile)).toBe('claude')
  })

  it('falls back to the default', () => {
    const profile = toConfigurationProfile({ model_id: 'claude' }, null, catalog)
    expect(effectiveModelId(profile)).toBe('claude')
  })

  it('is null when no model_id is set at all', () => {
    const profile = toConfigurationProfile({}, null, catalog)
    expect(effectiveModelId(profile)).toBeNull()
  })
})

describe('resolveEffectiveModel', () => {
  it('resolves the effective model id against the catalog', () => {
    const profile = toConfigurationProfile({ model_id: 'claude' }, null, catalog)
    expect(resolveEffectiveModel(profile)).toEqual(claude)
    expect(resolveModel(catalog, 'claude')).toEqual(claude)
  })

  it('is null when the effective id is not in the catalog or absent', () => {
    const profile = toConfigurationProfile({ model_id: 'missing' }, null, catalog)
    expect(resolveEffectiveModel(profile)).toBeNull()
    expect(resolveModel(catalog, null)).toBeNull()
  })
})
