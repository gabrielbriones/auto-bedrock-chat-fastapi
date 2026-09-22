import type { ConfigurationProfile } from '@/domains/model-config/domain/configuration-profile'
import { OVERRIDE_FIELDS } from '@/domains/model-config/domain/override-field'
import type { OverrideKey } from '@/domains/model-config/domain/override-key'
import { valueEqualsDefault } from '@/domains/model-config/domain/value-equals-default'

// FR-CFG-006: only keys that are (a) actually overridden and (b) differ from their default count.
// A key absent from `overrides` was never sent, or was reset by a `config_updated` with it absent
// from `active_overrides` — either way it is not "overridden".
export const overriddenKeys = (profile: ConfigurationProfile): readonly OverrideKey[] =>
  OVERRIDE_FIELDS.map((field) => field.key).filter((key) => {
    const overrideValue = profile.overrides[key]
    if (overrideValue === undefined) {
      return false
    }

    const defaultValue = profile.defaults[key]
    return defaultValue === undefined || !valueEqualsDefault(overrideValue, defaultValue)
  })

export const overrideCount = (profile: ConfigurationProfile): number => overriddenKeys(profile).length
