import { effective, type ConfigurationProfile } from '@/domains/model-config/domain/configuration-profile'
import { findModel, type ModelCatalog } from '@/domains/model-config/domain/model-catalog'
import type { ModelDescriptor } from '@/domains/model-config/domain/model-descriptor'

// `overrides.model_id ?? defaults.model_id` (DESIGN-001 §5.2 `EffectiveModel`).
export const effectiveModelId = (profile: ConfigurationProfile): string | null => {
  const value = effective(profile, 'model_id')
  return typeof value === 'string' ? value : null
}

export const resolveEffectiveModel = (profile: ConfigurationProfile): ModelDescriptor | null => {
  const modelId = effectiveModelId(profile)
  return modelId === null ? null : findModel(profile.catalog, modelId)
}

export const resolveModel = (catalog: ModelCatalog, modelId: string | null): ModelDescriptor | null =>
  modelId === null ? null : findModel(catalog, modelId)
