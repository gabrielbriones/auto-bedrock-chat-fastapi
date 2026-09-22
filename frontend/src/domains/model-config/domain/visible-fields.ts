import { effective, isAllowed, type ConfigurationProfile } from '@/domains/model-config/domain/configuration-profile'
import { resolveEffectiveModel } from '@/domains/model-config/domain/effective-model'
import { OVERRIDE_FIELDS } from '@/domains/model-config/domain/override-field'
import type { OverrideKey } from '@/domains/model-config/domain/override-key'

// FR-CFG-004 / I4: applies the allow-list, the model's temperature support, and the RAG
// dependency. Pure selector, recomputed by the caller on every profile change — replaces the
// legacy imperative `.hidden` toggling (DESIGN-001 §5.2 `visibleFields`).
export const visibleFields = (profile: ConfigurationProfile): readonly OverrideKey[] => {
  const effectiveModel = resolveEffectiveModel(profile)

  return OVERRIDE_FIELDS.filter((field) => {
    if (!isAllowed(profile, field.key)) {
      return false
    }

    if (field.requiresTemperatureSupport === true && effectiveModel?.supportsTemperature === false) {
      return false
    }

    if (field.dependsOn !== undefined) {
      return effective(profile, field.dependsOn.key) === field.dependsOn.requiredValue
    }

    return true
  }).map((field) => field.key)
}
