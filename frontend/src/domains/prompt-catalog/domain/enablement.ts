import { validateValue } from '@/domains/prompt-catalog/domain/validation-rule'
import type { PresetPrompt } from '@/domains/prompt-catalog/domain/preset-prompt'
import type { PromptVariable } from '@/domains/prompt-catalog/domain/prompt-variable'
import type { VariableValue } from '@/domains/prompt-catalog/domain/variable-value'

export type PresetEvaluation = {
  readonly enabled: boolean
  /** Required variables the catalogue has no definition or no binding for at all (defensive). */
  readonly missing: readonly string[]
  /** Required variables that are bound but fail their own `ValidationRule`. */
  readonly invalid: readonly string[]
}

// FR-PROMPT-005/012: a preset is enabled only when every one of its required variables validates.
export const evaluatePreset = (
  preset: PresetPrompt,
  variables: Readonly<Record<string, PromptVariable>>,
  bindings: Readonly<Record<string, VariableValue>>,
): PresetEvaluation => {
  const missing: string[] = []
  const invalid: string[] = []

  for (const name of preset.requiredVariables) {
    const variable = variables[name]
    const value = bindings[name]

    if (variable === undefined || value === undefined) {
      missing.push(name)
      continue
    }

    if (!validateValue(variable.validationRule, value)) {
      invalid.push(name)
    }
  }

  return { enabled: missing.length === 0 && invalid.length === 0, missing, invalid }
}
