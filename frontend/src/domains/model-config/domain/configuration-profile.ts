import { isOverrideKey, type OverrideKey, type OverrideValue } from '@/domains/model-config/domain/override-key'
import type { ModelCatalog } from '@/domains/model-config/domain/model-catalog'

// DESIGN-001 §5.2 aggregate root. `overrides` only ever changes via a server `config_updated`
// frame (I3) — nothing in this domain mutates it; that is the application layer's job (Phase 4).
export type ConfigurationProfile = {
  readonly defaults: Readonly<Partial<Record<OverrideKey, OverrideValue>>>
  readonly overrides: Readonly<Partial<Record<OverrideKey, OverrideValue>>>
  /** `null` means every `OverrideKey` is allowed (FR-CFG-002). */
  readonly allowedKeys: readonly OverrideKey[] | null
  readonly catalog: ModelCatalog
}

// `effective(key) = overrides[key] ?? defaults[key]` (DESIGN-001 §5.2).
export const effective = (profile: ConfigurationProfile, key: OverrideKey): OverrideValue | undefined =>
  profile.overrides[key] ?? profile.defaults[key]

export const isAllowed = (profile: ConfigurationProfile, key: OverrideKey): boolean =>
  profile.allowedKeys === null || profile.allowedKeys.includes(key)

const isOverrideValue = (value: unknown): value is OverrideValue =>
  typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'

// Anti-corruption boundary for `overrideDefaults` (raw, unvalidated per `bootstrap-config.dto.ts`):
// an unknown key or a wrong-typed value is dropped rather than thrown, matching the prompt-catalog
// domain's defensive-parse convention.
const toDefaults = (raw: Readonly<Record<string, unknown>>): Partial<Record<OverrideKey, OverrideValue>> => {
  const defaults: Partial<Record<OverrideKey, OverrideValue>> = {}

  for (const [key, value] of Object.entries(raw)) {
    if (isOverrideKey(key) && isOverrideValue(value)) {
      defaults[key] = value
    }
  }

  return defaults
}

// FR-CFG-002: an unknown key in the allow-list is ignored; `null` (or absent) means "all".
const toAllowedKeys = (raw: readonly string[] | null): readonly OverrideKey[] | null =>
  raw === null ? null : raw.filter(isOverrideKey)

export const toConfigurationProfile = (
  overrideDefaults: Readonly<Record<string, unknown>>,
  allowedDynamicOverrides: readonly string[] | null,
  catalog: ModelCatalog,
): ConfigurationProfile => ({
  defaults: toDefaults(overrideDefaults),
  overrides: {},
  allowedKeys: toAllowedKeys(allowedDynamicOverrides),
  catalog,
})
