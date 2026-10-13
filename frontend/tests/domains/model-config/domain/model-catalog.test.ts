import { describe, expect, it } from '@jest/globals'

import { buildModelCatalog, familyOf, findModel, modelDisplayName } from '@/domains/model-config/domain/model-catalog'
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

describe('modelDisplayName', () => {
  const catalog = buildModelCatalog(
    [
      model({ id: 'anthropic.claude-opus-5', name: 'Claude Opus 5' }),
      model({ id: 'us.anthropic.claude-sonnet-5', name: 'Claude Sonnet 5' }),
      model({ id: 'meta.llama3-1-70b-instruct-v1:0', name: 'Llama 3.1 70B Instruct', provider: 'meta' }),
    ],
    [],
  )

  it('maps a catalog model id to its display name', () => {
    expect(modelDisplayName(catalog, 'meta.llama3-1-70b-instruct-v1:0')).toBe('Llama 3.1 70B Instruct')
  })

  it('matches a region-prefixed id against a bare catalog id', () => {
    expect(modelDisplayName(catalog, 'us.anthropic.claude-opus-5')).toBe('Claude Opus 5')
  })

  it('matches a bare id against a region-prefixed catalog id', () => {
    expect(modelDisplayName(catalog, 'anthropic.claude-sonnet-5')).toBe('Claude Sonnet 5')
  })

  it('falls back to the raw id for an unknown model', () => {
    expect(modelDisplayName(catalog, 'openai.gpt-unknown')).toBe('openai.gpt-unknown')
  })

  it('does not treat a two-segment id as region-prefixed', () => {
    const bare = buildModelCatalog([model({ id: 'claude', name: 'Claude' })], [])
    expect(modelDisplayName(bare, 'us.claude')).toBe('us.claude')
  })
})
