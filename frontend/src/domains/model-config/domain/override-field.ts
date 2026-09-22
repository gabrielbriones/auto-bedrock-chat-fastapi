import { type OverrideKey } from '@/domains/model-config/domain/override-key'

export type OverrideControl = 'model-picker' | 'slider' | 'number' | 'switch'

// A field's dependency on another field's *effective* value, not its own — e.g. the two KB fields
// only render while `enable_rag` is on (SPEC-014 §2 model-dependent fields).
export type FieldDependency = { readonly key: OverrideKey; readonly requiredValue: boolean }

export type OverrideFieldDef = {
  readonly key: OverrideKey
  readonly control: OverrideControl
  readonly min?: number
  readonly max?: number
  readonly step?: number
  readonly hasHelp: boolean
  readonly dependsOn?: FieldDependency
  /** I4: hidden when the effective model reports no temperature support. */
  readonly requiresTemperatureSupport?: boolean
}

// SPEC-014 §1 table — order and constraints are parity with `DYNAMIC_OVERRIDE_FIELDS`.
export const OVERRIDE_FIELDS: readonly OverrideFieldDef[] = [
  { key: 'model_id', control: 'model-picker', hasHelp: false },
  { key: 'temperature', control: 'slider', min: 0, max: 1, step: 0.1, hasHelp: false, requiresTemperatureSupport: true },
  { key: 'top_p', control: 'slider', min: 0, max: 1, step: 0.1, hasHelp: false, requiresTemperatureSupport: true },
  { key: 'max_tokens', control: 'number', min: 1, max: 100_000, step: 1, hasHelp: false },
  { key: 'enable_ai_summarization', control: 'switch', hasHelp: true },
  { key: 'enable_rag', control: 'switch', hasHelp: false },
  {
    key: 'kb_top_k_results',
    control: 'number',
    min: 1,
    max: 20,
    step: 1,
    hasHelp: false,
    dependsOn: { key: 'enable_rag', requiredValue: true },
  },
  {
    key: 'kb_similarity_threshold',
    control: 'slider',
    min: 0,
    max: 1,
    step: 0.05,
    hasHelp: true,
    dependsOn: { key: 'enable_rag', requiredValue: true },
  },
] as const

export const overrideFieldFor = (key: OverrideKey): OverrideFieldDef => {
  // OVERRIDE_FIELDS declares every OverrideKey exactly once — a lookup miss is a data-shape bug.
  const field = OVERRIDE_FIELDS.find((candidate) => candidate.key === key)
  if (field === undefined) {
    throw new Error(`no OverrideFieldDef for key "${key}"`)
  }
  return field
}
