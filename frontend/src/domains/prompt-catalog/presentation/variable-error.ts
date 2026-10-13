import type { ValidationRule } from '@/domains/prompt-catalog/domain/validation-rule'
import { validateValue } from '@/domains/prompt-catalog/domain/validation-rule'
import type { VariableValue } from '@/domains/prompt-catalog/domain/variable-value'
import { PROMPT_CATALOG_COPY } from '@/shared/copy/prompt-catalog'

const { errors: ERROR_COPY } = PROMPT_CATALOG_COPY

// Phase 1 accept: "a required variable left empty is reported at the field". One message per
// rule kind so a numeric range names its bounds instead of a generic "required".
export const variableFieldError = (rule: ValidationRule, value: VariableValue): string | null => {
  if (validateValue(rule, value)) {
    return null
  }

  if (rule.kind === 'numericRange') {
    if (rule.min !== null && rule.max !== null) {
      return ERROR_COPY.numberRange(rule.min, rule.max)
    }
    if (rule.min !== null) {
      return ERROR_COPY.numberMin(rule.min)
    }
    if (rule.max !== null) {
      return ERROR_COPY.numberMax(rule.max)
    }
    return ERROR_COPY.numberInvalid
  }

  return ERROR_COPY.required
}
