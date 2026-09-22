import { normalizeInputType, type PromptInputType } from '@/domains/prompt-catalog/domain/input-type'
import { deriveLabel } from '@/domains/prompt-catalog/domain/label'
import { parseDetectionRule, type DetectionRule } from '@/domains/prompt-catalog/domain/detection-rule'
import { parseValidationRule, type ValidationRule } from '@/domains/prompt-catalog/domain/validation-rule'
import type { VariableValue } from '@/domains/prompt-catalog/domain/variable-value'

export type SelectOption = {
  readonly value: string
  readonly label: string
}

export type PromptVariable = {
  readonly name: string
  readonly label: string
  readonly inputType: PromptInputType
  readonly placeholder: string | null
  readonly defaultValue: string | null
  readonly options: readonly SelectOption[]
  readonly min: number | null
  readonly max: number | null
  readonly step: number | null
  readonly validationRule: ValidationRule
  readonly detection: DetectionRule | null
}

// CONTRACT-001 §6: `variables[]` arrives as an array of opaque, still-snake_case records — the
// bootstrap DTO deliberately doesn't interpret them (see `chat-bootstrap.ts`). This module is the
// anti-corruption layer that does.
export type PromptVariableWireConfig = Readonly<Record<string, unknown>>

export type ParsedPromptVariable = {
  readonly variable: PromptVariable
  readonly diagnostics: readonly string[]
}

const asString = (config: PromptVariableWireConfig, key: string): string | null => {
  const value = config[key]
  return typeof value === 'string' ? value : null
}

const asNumber = (config: PromptVariableWireConfig, key: string): number | null => {
  const value = config[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

const isOptionRecord = (value: unknown): value is { value: string; label: string } =>
  typeof value === 'object' &&
  value !== null &&
  typeof (value as Record<string, unknown>).value === 'string' &&
  typeof (value as Record<string, unknown>).label === 'string'

const asOptions = (config: PromptVariableWireConfig): readonly SelectOption[] => {
  const value = config.options
  if (!Array.isArray(value)) {
    return []
  }

  return value.flatMap((option): readonly SelectOption[] => {
    if (typeof option === 'string') {
      return [{ value: option, label: option }]
    }

    return isOptionRecord(option) ? [{ value: option.value, label: option.label }] : []
  })
}

// FR-PROMPT-002/003/004: a variable with no `name` is malformed and cannot be rendered or bound to
// any template placeholder — skip it with a diagnostic rather than producing a nameless field.
export const parsePromptVariable = (config: PromptVariableWireConfig): ParsedPromptVariable | null => {
  const name = asString(config, 'name')
  if (name === null || name.length === 0) {
    return null
  }

  const inputType = normalizeInputType(asString(config, 'input_type'))
  const min = asNumber(config, 'min')
  const max = asNumber(config, 'max')
  const { rule, diagnostic } = parseValidationRule(inputType, asString(config, 'validate'), min, max, name)

  const variable: PromptVariable = {
    name,
    label: asString(config, 'label') ?? deriveLabel(name),
    inputType,
    placeholder: asString(config, 'placeholder'),
    defaultValue: asString(config, 'default'),
    options: asOptions(config),
    min,
    max,
    step: asNumber(config, 'step'),
    validationRule: rule,
    detection: parseDetectionRule(inputType, asString(config, 'detect_pattern'), asString(config, 'detect_flags'), rule),
  }

  return { variable, diagnostics: diagnostic === null ? [] : [diagnostic] }
}

// Docs (`preset-prompts.md` §"Variable Definitions"): a `{{PLACEHOLDER}}` with no matching entry
// under `variables:` is auto-inferred as a plain required text field.
export const inferPromptVariable = (name: string): PromptVariable => ({
  name,
  label: deriveLabel(name),
  inputType: 'text',
  placeholder: null,
  defaultValue: null,
  options: [],
  min: null,
  max: null,
  step: null,
  validationRule: { kind: 'nonEmpty' },
  detection: null,
})

// FR-PROMPT-013: the initial binding a variable starts with — its `default`, or the empty/NaN
// value for its kind. `number` uses `NaN` (never `0`) so an unset numeric field reports invalid
// rather than looking like a deliberate zero.
export const defaultBindingFor = (variable: PromptVariable): VariableValue => {
  switch (variable.inputType) {
    case 'checkbox':
      return { kind: 'boolean', value: variable.defaultValue === 'true' }
    case 'number':
      return {
        kind: 'number',
        value: variable.defaultValue === null ? Number.NaN : Number(variable.defaultValue),
      }
    case 'select':
      return { kind: 'select', value: variable.defaultValue ?? '' }
    case 'text':
      return { kind: 'text', value: variable.defaultValue ?? '' }
  }
}

// FR-PROMPT-008: a deep-link query value is always a raw string; this converts it into the shape
// the variable's own kind expects (checkbox accepts `"true"` or `"1"`).
export const bindingFromText = (variable: PromptVariable, raw: string): VariableValue => {
  switch (variable.inputType) {
    case 'checkbox':
      return { kind: 'boolean', value: raw === 'true' || raw === '1' }
    case 'number':
      return { kind: 'number', value: Number(raw) }
    case 'select':
      return { kind: 'select', value: raw }
    case 'text':
      return { kind: 'text', value: raw }
  }
}
