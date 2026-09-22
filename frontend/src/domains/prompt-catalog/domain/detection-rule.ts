import type { PromptInputType } from '@/domains/prompt-catalog/domain/input-type'
import type { ValidationRule } from '@/domains/prompt-catalog/domain/validation-rule'

export type DetectionRule = {
  readonly pattern: string
  readonly flags: string
}

const DEFAULT_FLAGS = 'i'

// FR-PROMPT-007 / FR-PROMPT-007b: `detect_pattern` wins when given; otherwise falls back to the
// field's own `validate` regex with anchors stripped (parity with the legacy client, which reused
// `validate` when no explicit `detect_pattern` was configured). Never applies outside `text` —
// select/checkbox/number are never auto-detected. Compile-safety of the resulting pattern is the
// concern of whatever runs detection (Phase 2), not this parse step.
export const parseDetectionRule = (
  inputType: PromptInputType,
  detectPattern: string | null,
  detectFlags: string | null,
  validationRule: ValidationRule,
): DetectionRule | null => {
  if (inputType !== 'text') {
    return null
  }

  if (detectPattern !== null) {
    return { pattern: detectPattern, flags: detectFlags ?? DEFAULT_FLAGS }
  }

  if (validationRule.kind === 'regex' && !validationRule.invalid) {
    const pattern = validationRule.source.replace(/^\^/, '').replace(/\$$/, '')
    return { pattern, flags: detectFlags ?? DEFAULT_FLAGS }
  }

  return null
}
