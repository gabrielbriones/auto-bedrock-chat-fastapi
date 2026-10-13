import { err, ok, type Result } from '@/shared/kernel/result'

import type { PresetPrompt } from '@/domains/prompt-catalog/domain/preset-prompt'
import type { VariableValue } from '@/domains/prompt-catalog/domain/variable-value'

export type ComposedPrompt = {
  readonly text: string
  readonly presetId: string
  readonly bindings: Readonly<Record<string, VariableValue>>
}

export type MissingVariables = {
  readonly kind: 'missing-variables'
  readonly names: readonly string[]
}

// FR-PROMPT-006a: substituted verbatim — no escaping, quoting or trimming beyond what validation
// already enforced.
const toText = (value: VariableValue): string => {
  switch (value.kind) {
    case 'text':
    case 'select':
      return value.value
    case 'number':
      return String(value.value)
    case 'boolean':
      return value.value ? 'true' : 'false'
  }
}

// FR-PROMPT-006/014: every occurrence of every `{{NAME}}` placeholder is substituted; a preset
// missing any required binding composes nothing and names what's missing rather than sending a
// half-filled template.
export const composePreset = (
  preset: PresetPrompt,
  bindings: Readonly<Record<string, VariableValue>>,
): Result<ComposedPrompt, MissingVariables> => {
  const missing = preset.requiredVariables.filter((name) => bindings[name] === undefined)
  if (missing.length > 0) {
    return err({ kind: 'missing-variables', names: missing })
  }

  const text = preset.requiredVariables.reduce((template, name) => {
    const value = bindings[name]
    return value === undefined ? template : template.replaceAll(`{{${name}}}`, toText(value))
  }, preset.template)

  const usedBindings: Record<string, VariableValue> = {}
  for (const name of preset.requiredVariables) {
    const value = bindings[name]
    if (value !== undefined) {
      usedBindings[name] = value
    }
  }

  return ok({ text, presetId: preset.id, bindings: usedBindings })
}
