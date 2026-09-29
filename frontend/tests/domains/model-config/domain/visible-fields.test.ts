import { describe, expect, it } from '@jest/globals'

import { buildModelCatalog } from '@/domains/model-config/domain/model-catalog'
import { toConfigurationProfile } from '@/domains/model-config/domain/configuration-profile'
import type { ModelDescriptor } from '@/domains/model-config/domain/model-descriptor'
import { visibleFields } from '@/domains/model-config/domain/visible-fields'

const modelWithTemp: ModelDescriptor = {
  id: 'claude',
  name: 'Claude',
  provider: 'anthropic',
  supportsTemperature: true,
  maxOutputTokens: 4096,
}
const modelWithoutTemp: ModelDescriptor = { ...modelWithTemp, id: 'no-temp', supportsTemperature: false }
const catalog = buildModelCatalog([modelWithTemp, modelWithoutTemp], [])

describe('visibleFields', () => {
  it('renders every field for an allowed model that supports temperature, RAG off', () => {
    const profile = toConfigurationProfile({ model_id: 'claude', enable_rag: false }, null, catalog)
    const fields = visibleFields(profile)

    expect(fields).toContain('temperature')
    expect(fields).toContain('top_p')
    expect(fields).not.toContain('kb_top_k_results')
    expect(fields).not.toContain('kb_similarity_threshold')
  })

  it('hides temperature and top_p when the effective model does not support it (I4)', () => {
    const profile = toConfigurationProfile({ model_id: 'no-temp' }, null, catalog)
    const fields = visibleFields(profile)

    expect(fields).not.toContain('temperature')
    expect(fields).not.toContain('top_p')
  })

  it('shows the KB fields once enable_rag is on', () => {
    const profile = toConfigurationProfile({ model_id: 'claude', enable_rag: true }, null, catalog)
    const fields = visibleFields(profile)

    expect(fields).toContain('kb_top_k_results')
    expect(fields).toContain('kb_similarity_threshold')
  })

  it('respects the allow-list (FR-CFG-002)', () => {
    const profile = toConfigurationProfile({ model_id: 'claude' }, ['model_id', 'max_tokens'], catalog)
    expect(visibleFields(profile)).toEqual(['model_id', 'max_tokens'])
  })

  it('does not hide temperature/top_p when there is no resolved effective model', () => {
    const profile = toConfigurationProfile({}, null, catalog)
    const fields = visibleFields(profile)

    expect(fields).toContain('temperature')
    expect(fields).toContain('top_p')
  })
})
