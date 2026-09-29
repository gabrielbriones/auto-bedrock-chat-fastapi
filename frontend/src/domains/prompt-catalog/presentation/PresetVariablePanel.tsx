import { PROMPT_CATALOG_COPY } from '@/shared/copy/prompt-catalog'

import type { PromptVariable } from '@/domains/prompt-catalog/domain/prompt-variable'
import type { VariableValue } from '@/domains/prompt-catalog/domain/variable-value'
import { variableFieldError } from '@/domains/prompt-catalog/presentation/variable-error'
import { VariableField } from '@/domains/prompt-catalog/presentation/VariableField'

export type PresetVariablePanelProps = {
  /** The union of every variable name any preset in the bar might need (`allRequiredVariableNames`) — legacy never scoped this panel to a single preset. */
  readonly variableNames: readonly string[]
  readonly variables: Readonly<Record<string, PromptVariable>>
  readonly bindings: Readonly<Record<string, VariableValue>>
  readonly detected?: ReadonlySet<string>
  readonly showValidationErrors?: boolean
  readonly onBindingChange: (name: string, value: VariableValue) => void
}

// FR-PROMPT-002/011/012/013: one control per variable, each validated independently from domain
// state. No variables at all renders nothing — no empty container, no layout shift.
export function PresetVariablePanel({
  variableNames,
  variables,
  bindings,
  detected = new Set(),
  showValidationErrors = false,
  onBindingChange,
}: PresetVariablePanelProps) {
  if (variableNames.length === 0) {
    return null
  }

  return (
    <fieldset className="grid min-w-0 gap-4 sm:grid-cols-2">
      <legend className="sr-only">{PROMPT_CATALOG_COPY.variablePanel.label}</legend>
      {variableNames.map((name) => {
        const variable = variables[name]
        if (variable === undefined) {
          return null
        }

        const value = bindings[name]
        if (value === undefined) {
          return null
        }

        return (
          <div key={name} className="min-w-0">
            <VariableField
              variable={variable}
              value={value}
              error={showValidationErrors ? variableFieldError(variable.validationRule, value) : null}
              detected={detected.has(name)}
              onChange={(next) => onBindingChange(name, next)}
            />
          </div>
        )
      })}
    </fieldset>
  )
}
