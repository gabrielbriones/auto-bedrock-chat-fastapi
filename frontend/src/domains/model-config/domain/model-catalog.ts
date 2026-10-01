import type { ModelDescriptor } from '@/domains/model-config/domain/model-descriptor'

export type ModelFamily = {
  readonly provider: string
  readonly models: readonly ModelDescriptor[]
}

export type ModelCatalog = {
  readonly families: readonly ModelFamily[]
}

// Parity fallback name for a model with no provider at all (blank string) — SPEC-014 §2 FR-CFG-003.
const UNPROVIDERED_FAMILY = 'Models'

const groupByProvider = (models: readonly ModelDescriptor[]): readonly ModelFamily[] => {
  const byProvider = new Map<string, ModelDescriptor[]>()

  for (const model of models) {
    const provider = model.provider === '' ? UNPROVIDERED_FAMILY : model.provider
    const bucket = byProvider.get(provider)
    if (bucket === undefined) {
      byProvider.set(provider, [model])
    } else {
      bucket.push(model)
    }
  }

  return Array.from(byProvider.entries(), ([provider, groupedModels]) => ({ provider, models: groupedModels }))
}

// FR-CFG-003: families come from `availableModelGroups`; an empty/absent catalogue falls back to
// grouping `availableModels` by `provider`.
export const buildModelCatalog = (
  models: readonly ModelDescriptor[],
  groups: readonly ModelFamily[],
): ModelCatalog => ({
  families: groups.length > 0 ? groups : groupByProvider(models),
})

export const findModel = (catalog: ModelCatalog, modelId: string): ModelDescriptor | null => {
  for (const family of catalog.families) {
    const match = family.models.find((model) => model.id === modelId)
    if (match !== undefined) {
      return match
    }
  }
  return null
}

export const familyOf = (catalog: ModelCatalog, modelId: string): ModelFamily | null =>
  catalog.families.find((family) => family.models.some((model) => model.id === modelId)) ?? null

// Mirrors the backend's MODEL_ID_REGION_PREFIXES (autolangchat/config.py).
const REGION_PREFIXES: ReadonlySet<string> = new Set(['us', 'eu', 'au', 'jp', 'global'])

const bareModelId = (modelId: string): string => {
  const [prefix = '', ...rest] = modelId.split('.')
  return rest.length > 1 && REGION_PREFIXES.has(prefix) ? rest.join('.') : modelId
}

// Recorded ids may carry a cross-region inference-profile prefix the catalog entry lacks, or vice
// versa; unknown ids fall back to the raw id.
export const modelDisplayName = (catalog: ModelCatalog, modelId: string): string => {
  const exact = findModel(catalog, modelId)
  if (exact !== null) {
    return exact.name
  }
  const bare = bareModelId(modelId)
  for (const family of catalog.families) {
    const match = family.models.find((model) => bareModelId(model.id) === bare)
    if (match !== undefined) {
      return match.name
    }
  }
  return modelId
}
