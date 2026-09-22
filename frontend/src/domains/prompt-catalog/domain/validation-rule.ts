import type { PromptInputType } from '@/domains/prompt-catalog/domain/input-type'
import type { VariableValue } from '@/domains/prompt-catalog/domain/variable-value'

// SPEC-013 §1 / DESIGN-001 §5.1. `select` and `text` both reduce to `nonEmpty` — they differ only
// in how the control renders (VariableField), never in how they validate.
export type ValidationRule =
  | { readonly kind: 'always' }
  | { readonly kind: 'nonEmpty' }
  | { readonly kind: 'regex'; readonly source: string; readonly invalid: boolean }
  | { readonly kind: 'numericRange'; readonly min: number | null; readonly max: number | null }

export type ParsedValidationRule = {
  readonly rule: ValidationRule
  /** FR-PROMPT-004a: set exactly when `validate` is an uncompilable regex; `null` otherwise. */
  readonly diagnostic: string | null
}

const isCompilable = (source: string): boolean => {
  try {
    return Boolean(new RegExp(source))
  } catch {
    return false
  }
}

// FR-PROMPT-004: checkbox always validates; number is a range check; select is non-empty; text
// uses `validate` (`"nonempty"`, unset, or a regex) — parity with the legacy `_validateVar`.
export const parseValidationRule = (
  inputType: PromptInputType,
  validate: string | null,
  min: number | null,
  max: number | null,
  variableName: string,
): ParsedValidationRule => {
  if (inputType === 'checkbox') {
    return { rule: { kind: 'always' }, diagnostic: null }
  }

  if (inputType === 'number') {
    return { rule: { kind: 'numericRange', min, max }, diagnostic: null }
  }

  if (inputType === 'select') {
    return { rule: { kind: 'nonEmpty' }, diagnostic: null }
  }

  if (validate === null || validate === 'nonempty') {
    return { rule: { kind: 'nonEmpty' }, diagnostic: null }
  }

  const invalid = !isCompilable(validate)

  return {
    rule: { kind: 'regex', source: validate, invalid },
    diagnostic: invalid ? `Invalid validate pattern for variable "${variableName}"` : null,
  }
}

// FR-PROMPT-013: always derived from domain state, never the DOM.
export const validateValue = (rule: ValidationRule, value: VariableValue): boolean => {
  switch (rule.kind) {
    case 'always':
      return true
    case 'nonEmpty':
      return (
        (value.kind === 'text' || value.kind === 'select') && value.value.trim().length > 0
      )
    case 'regex':
      return (
        !rule.invalid && value.kind === 'text' && new RegExp(rule.source).test(value.value.trim())
      )
    case 'numericRange':
      return (
        value.kind === 'number' &&
        Number.isFinite(value.value) &&
        (rule.min === null || value.value >= rule.min) &&
        (rule.max === null || value.value <= rule.max)
      )
  }
}
