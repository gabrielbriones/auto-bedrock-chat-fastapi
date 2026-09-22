import { describe, expect, it } from 'vitest'

import { buildModelCatalog, familyOf, findModel } from '@/domains/model-config/domain/model-catalog'
import type { ModelDescriptor } from '@/domains/model-config/domain/model-descriptor'

const model = (overrides: Partial<ModelDescriptor> = {}): ModelDescriptor => ({
  id: 'claude',
  name: 'Claude',
  provider: 'anthropic',
  supportsTemperature: true,
  maxOutputTokens: 4096,
  ...overrides,
})

describe('buildModelCatalog', () => {
  it('uses availableModelGroups when present', () => {
    const groups = [{ provider: 'anthropic', models: [model()] }]
    const catalog = buildModelCatalog([], groups)
    expect(catalog.families).toBe(groups)
  })

  it('falls back to grouping availableModels by provider when groups are empty (FR-CFG-003)', () => {
    const models = [model({ id: 'a', provider: 'anthropic' }), model({ id: 'b', provider: 'openai' })]
    const catalog = buildModelCatalog(models, [])

    expect(catalog.families).toEqual([
      { provider: 'anthropic', models: [models[0]] },
      { provider: 'openai', models: [models[1]] },
    ])
  })

  it('defaults a blank provider to "Models"', () => {
    const models = [model({ id: 'a', provider: '' })]
    const catalog = buildModelCatalog(models, [])

    expect(catalog.families).toEqual([{ provider: 'Models', models: [models[0]] }])
  })
})

describe('findModel', () => {
  it('finds a model across every family', () => {
    const target = model({ id: 'b', provider: 'openai' })
    const catalog = buildModelCatalog([model({ id: 'a' }), target], [])

    expect(findModel(catalog, 'b')).toEqual(target)
  })

  it('returns null when no model matches', () => {
    const catalog = buildModelCatalog([model()], [])
    expect(findModel(catalog, 'missing')).toBeNull()
  })
})

describe('familyOf', () => {
  it('returns the family containing the given model id', () => {
    const catalog = buildModelCatalog([model({ id: 'a', provider: 'anthropic' })], [])
    expect(familyOf(catalog, 'a')?.provider).toBe('anthropic')
  })

  it('returns null when no family contains the model', () => {
    const catalog = buildModelCatalog([model()], [])
    expect(familyOf(catalog, 'missing')).toBeNull()
  })
})
