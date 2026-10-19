import { describe, expect, it } from '@jest/globals'

import {
  baseModels,
  buildModelCatalog,
  familyOf,
  findModel,
  modelDisplayName,
  regionVariants,
  representativeModel,
  withDefaultRegion,
} from '@/domains/model-config/domain/model-catalog'
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

// XMGPLAT-12046: the region-less ("Default") variant must resolve to the server's default region.
describe('default region', () => {
  const sonnet = [
    model({ id: 'anthropic.claude-sonnet-5', name: 'Claude Sonnet 5' }),
    model({ id: 'eu.anthropic.claude-sonnet-5', name: 'Claude Sonnet 5 (EU)' }),
    model({ id: 'global.anthropic.claude-sonnet-5', name: 'Claude Sonnet 5 (Global)' }),
    model({ id: 'us.anthropic.claude-sonnet-5', name: 'Claude Sonnet 5 (US)' }),
  ]
  const llama = model({ id: 'meta.llama3-3-70b-instruct-v1:0', name: 'Llama 3.3', provider: 'meta' })
  const ids = (models: readonly { id: string }[]) => models.map((entry) => entry.id)

  describe('withDefaultRegion', () => {
    it('maps a bare id to its default-region variant', () => {
      expect(withDefaultRegion(buildModelCatalog(sonnet, [], 'global'), 'anthropic.claude-sonnet-5')).toBe(
        'global.anthropic.claude-sonnet-5',
      )
      expect(withDefaultRegion(buildModelCatalog(sonnet, [], 'us'), 'anthropic.claude-sonnet-5')).toBe(
        'us.anthropic.claude-sonnet-5',
      )
    })

    it('passes an explicitly selected region through unchanged', () => {
      const catalog = buildModelCatalog(sonnet, [], 'global')
      expect(withDefaultRegion(catalog, 'eu.anthropic.claude-sonnet-5')).toBe('eu.anthropic.claude-sonnet-5')
    })

    it('keeps a bare id with no default-region variant in the catalog', () => {
      expect(withDefaultRegion(buildModelCatalog([llama], [], 'global'), llama.id)).toBe(llama.id)
    })

    it('is a no-op without a default region (older backends)', () => {
      expect(withDefaultRegion(buildModelCatalog(sonnet, []), 'anthropic.claude-sonnet-5')).toBe(
        'anthropic.claude-sonnet-5',
      )
    })
  })

  describe('regionVariants', () => {
    it('hides the "Default" entry and lists the default region first', () => {
      const catalog = buildModelCatalog(sonnet, [], 'us')
      expect(ids(regionVariants(catalog, 'eu.anthropic.claude-sonnet-5'))).toEqual([
        'us.anthropic.claude-sonnet-5',
        'eu.anthropic.claude-sonnet-5',
        'global.anthropic.claude-sonnet-5',
      ])
    })

    it('keeps the "Default" entry when the default region has no variant', () => {
      const catalog = buildModelCatalog(sonnet, [], 'jp')
      expect(ids(regionVariants(catalog, 'us.anthropic.claude-sonnet-5'))[0]).toBe('anthropic.claude-sonnet-5')
    })

    it('keeps the "Default" entry without a default region', () => {
      const catalog = buildModelCatalog(sonnet, [])
      expect(ids(regionVariants(catalog, 'us.anthropic.claude-sonnet-5'))).toHaveLength(4)
    })
  })

  describe('representativeModel / baseModels', () => {
    it('still represents the model by its region-less entry', () => {
      const catalog = buildModelCatalog(sonnet, [], 'global')
      expect(representativeModel(catalog, 'eu.anthropic.claude-sonnet-5')?.name).toBe('Claude Sonnet 5')
      expect(ids(baseModels({ provider: 'anthropic', models: sonnet }, 'global'))).toEqual(['anthropic.claude-sonnet-5'])
    })

    it('prefers the default-region variant when there is no region-less entry', () => {
      const prefixed = sonnet.slice(1)
      expect(ids(baseModels({ provider: 'anthropic', models: prefixed }, 'us'))).toEqual(['us.anthropic.claude-sonnet-5'])
      expect(representativeModel(buildModelCatalog(prefixed, [], 'us'), 'eu.anthropic.claude-sonnet-5')?.id).toBe(
        'us.anthropic.claude-sonnet-5',
      )
    })
  })
})
