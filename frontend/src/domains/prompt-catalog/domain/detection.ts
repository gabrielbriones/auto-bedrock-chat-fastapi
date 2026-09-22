import type { PromptVariable } from '@/domains/prompt-catalog/domain/prompt-variable'
import type { VariableValue } from '@/domains/prompt-catalog/domain/variable-value'

const isCompilable = (pattern: string): boolean => {
  try {
    return Boolean(new RegExp(pattern))
  } catch {
    return false
  }
}

// FR-PROMPT-007/007a/007b: only `text` variables with a detection rule are ever scanned, and only
// those the user hasn't already edited explicitly — auto-detection never overrides an explicit
// selection (Phase 2 accept). Returns just the bindings to merge; applies no side effect itself.
export const detectBindings = (
  message: string,
  variables: Readonly<Record<string, PromptVariable>>,
  editedNames: ReadonlySet<string>,
): Readonly<Record<string, VariableValue>> => {
  const detected: Record<string, VariableValue> = {}

  for (const variable of Object.values(variables)) {
    if (variable.inputType !== 'text' || variable.detection === null || editedNames.has(variable.name)) {
      continue
    }

    const { pattern, flags } = variable.detection
    if (!isCompilable(pattern)) {
      continue
    }

    const match = new RegExp(pattern, flags).exec(message)
    if (match !== null) {
      detected[variable.name] = { kind: 'text', value: match[0] }
    }
  }

  return detected
}
