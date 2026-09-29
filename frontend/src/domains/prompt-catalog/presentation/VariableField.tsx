import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { PROMPT_CATALOG_COPY } from '@/shared/copy/prompt-catalog'

import type { PromptVariable } from '@/domains/prompt-catalog/domain/prompt-variable'
import type { VariableValue } from '@/domains/prompt-catalog/domain/variable-value'
import { DetectedValueBadge } from '@/domains/prompt-catalog/presentation/DetectedValueBadge'

export type VariableFieldProps = {
  readonly variable: PromptVariable
  readonly value: VariableValue
  /** `null` when the current value validates; shown and aria-linked otherwise. */
  readonly error: string | null
  /** FR-PROMPT-015: shows a badge until the user edits the field. Never true for non-text variables. */
  readonly detected?: boolean
  readonly onChange: (value: VariableValue) => void
}

type FieldChrome = {
  readonly id: string
  readonly errorId: string
  readonly described: Readonly<Record<string, unknown>>
}

const fieldChrome = (name: string, error: string | null): FieldChrome => {
  const id = `prompt-var-${name}`
  const errorId = `${id}-error`
  return {
    id,
    errorId,
    described: error === null ? {} : { 'aria-invalid': true, 'aria-describedby': errorId },
  }
}

const FieldError = ({ id, message }: { readonly id: string; readonly message: string | null }) =>
  message === null ? null : (
    <p id={id} role="alert" className="text-sm text-destructive">
      {message}
    </p>
  )

const FieldLabel = ({ htmlFor, label, detected }: { readonly htmlFor: string; readonly label: string; readonly detected: boolean }) => (
  <div className="flex items-center gap-2">
    <Label htmlFor={htmlFor}>{label}</Label>
    {detected ? <DetectedValueBadge /> : null}
  </div>
)

type ControlProps = {
  readonly variable: PromptVariable
  readonly value: VariableValue
  readonly chrome: FieldChrome
  readonly detected: boolean
  readonly onChange: (value: VariableValue) => void
}

function CheckboxControl({ variable, value, chrome, onChange }: ControlProps) {
  const checked = value.kind === 'boolean' && value.value
  return (
    <div className="flex items-center gap-2">
      <Checkbox
        id={chrome.id}
        checked={checked}
        onCheckedChange={(next) => onChange({ kind: 'boolean', value: next === true })}
      />
      <Label htmlFor={chrome.id}>{variable.label}</Label>
    </div>
  )
}

function SelectControl({ variable, value, chrome, detected, onChange, error }: ControlProps & { readonly error: string | null }) {
  const current = value.kind === 'select' ? value.value : ''
  const selectedLabel = variable.options.find((option) => option.value === current)?.label

  return (
    <div className="grid gap-2">
      <FieldLabel htmlFor={chrome.id} label={variable.label} detected={detected} />
      <Select
        value={current === '' ? null : current}
        onValueChange={(next: string | null) => onChange({ kind: 'select', value: next ?? '' })}
      >
        <SelectTrigger id={chrome.id} className="w-full" {...chrome.described}>
          <SelectValue>
            {current === '' ? (variable.placeholder ?? PROMPT_CATALOG_COPY.selectPlaceholder(variable.label)) : (selectedLabel ?? current)}
          </SelectValue>
        </SelectTrigger>
        <SelectContent>
          {variable.options.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <FieldError id={chrome.errorId} message={error} />
    </div>
  )
}

function NumberControl({ variable, value, chrome, detected, onChange, error }: ControlProps & { readonly error: string | null }) {
  const current = value.kind === 'number' && Number.isFinite(value.value) ? String(value.value) : ''
  return (
    <div className="grid gap-2">
      <FieldLabel htmlFor={chrome.id} label={variable.label} detected={detected} />
      <Input
        id={chrome.id}
        type="number"
        value={current}
        placeholder={variable.placeholder ?? undefined}
        min={variable.min ?? undefined}
        max={variable.max ?? undefined}
        step={variable.step ?? undefined}
        onChange={(event) => onChange({ kind: 'number', value: Number(event.target.value) })}
        {...chrome.described}
      />
      <FieldError id={chrome.errorId} message={error} />
    </div>
  )
}

function TextControl({ variable, value, chrome, detected, onChange, error }: ControlProps & { readonly error: string | null }) {
  const current = value.kind === 'text' ? value.value : ''
  return (
    <div className="grid gap-2">
      <FieldLabel htmlFor={chrome.id} label={variable.label} detected={detected} />
      <Input
        id={chrome.id}
        type="text"
        value={current}
        placeholder={variable.placeholder ?? undefined}
        onChange={(event) => onChange({ kind: 'text', value: event.target.value })}
        {...chrome.described}
      />
      <FieldError id={chrome.errorId} message={error} />
    </div>
  )
}

// FR-PROMPT-002: dispatches on `inputType`; every control renders and validates independently, and
// an unrecognised type has already degraded to `text` at parse time (`normalizeInputType`).
export function VariableField({ variable, value, error, detected = false, onChange }: VariableFieldProps) {
  const chrome = fieldChrome(variable.name, error)
  const control = { variable, value, chrome, detected, onChange, error }

  switch (variable.inputType) {
    case 'checkbox':
      return <CheckboxControl {...control} />
    case 'select':
      return <SelectControl {...control} />
    case 'number':
      return <NumberControl {...control} />
    case 'text':
      return <TextControl {...control} />
  }
}
