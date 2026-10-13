import { overrideFieldFor } from '@/domains/model-config/domain/override-field'
import type { ModelDescriptor } from '@/domains/model-config/domain/model-descriptor'

const MAX_TOKENS_FIELD = overrideFieldFor('max_tokens')

// FR-CFG-005: the maximum follows `effectiveModel.max_output_tokens`, falling back to the field's
// own maximum when there is no resolved model yet.
export const maxTokensCap = (effectiveModel: ModelDescriptor | null): number =>
  effectiveModel?.maxOutputTokens ?? MAX_TOKENS_FIELD.max ?? Number.POSITIVE_INFINITY

// FR-CFG-005/005a: clamps into `[min, cap]`, re-evaluated on every model switch.
export const clampMaxTokens = (value: number, effectiveModel: ModelDescriptor | null): number => {
  const cap = maxTokensCap(effectiveModel)
  const min = MAX_TOKENS_FIELD.min ?? 1
  return Math.min(Math.max(value, min), cap)
}

// FR-CFG-018: tells the caller whether the clamp actually changed anything, so it only warns then.
export const wasClamped = (value: number, effectiveModel: ModelDescriptor | null): boolean =>
  clampMaxTokens(value, effectiveModel) !== value
