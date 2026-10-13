import { useId, type ReactNode } from 'react'

import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { KNOWLEDGE_COPY } from '@/shared/copy/knowledge'

import type { KbSourceIssue } from '@/domains/knowledge/domain/public'

// The field building blocks both ingestion forms share: a label, the control, an optional hint and
// the issue the domain validator raised for it, wired together with `useId` so a second mounted
// form can never claim the same element.
type FieldFrameProps = {
  readonly label: string
  readonly hint?: string
  readonly issue?: KbSourceIssue | undefined
  readonly children: (ids: { readonly id: string; readonly describedBy: string | undefined; readonly invalid: true | undefined }) => ReactNode
}

export function FieldFrame({ label, hint, issue, children }: FieldFrameProps) {
  const id = useId()
  const hintId = `${id}-hint`
  const issueId = `${id}-issue`
  const describedBy = [hint === undefined ? null : hintId, issue === undefined ? null : issueId]
    .filter((value) => value !== null)
    .join(' ')

  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>{label}</Label>
      {children({ id, describedBy: describedBy.length > 0 ? describedBy : undefined, invalid: issue === undefined ? undefined : true })}
      {hint === undefined ? null : <p id={hintId} className="text-sm text-muted-foreground">{hint}</p>}
      {issue === undefined ? null : (
        <p id={issueId} role="alert" className="text-sm text-destructive">{KNOWLEDGE_COPY.sources.issues[issue]}</p>
      )}
    </div>
  )
}

type DraftTextFieldProps = {
  readonly label: string
  readonly value: string
  readonly onChange: (value: string) => void
  readonly disabled: boolean
  readonly hint?: string
  readonly issue?: KbSourceIssue | undefined
  readonly type?: 'text' | 'number'
  readonly min?: number
  readonly max?: number
}

export function DraftTextField({ label, value, onChange, disabled, hint, issue, type = 'text', min, max }: DraftTextFieldProps) {
  return (
    <FieldFrame label={label} {...(hint === undefined ? {} : { hint })} issue={issue}>
      {({ id, describedBy, invalid }) => (
        <Input
          id={id}
          type={type}
          inputMode={type === 'number' ? 'numeric' : undefined}
          min={min}
          max={max}
          value={value}
          disabled={disabled}
          aria-invalid={invalid}
          aria-describedby={describedBy}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </FieldFrame>
  )
}

type DraftCheckboxProps = {
  readonly label: string
  readonly hint: string
  readonly checked: boolean
  readonly onChange: (checked: boolean) => void
  readonly disabled: boolean
}

export function DraftCheckbox({ label, hint, checked, onChange, disabled }: DraftCheckboxProps) {
  const id = useId()
  const hintId = `${id}-hint`

  return (
    <div className="grid gap-2">
      <div className="flex items-center gap-2">
        <Checkbox
          id={id}
          checked={checked}
          disabled={disabled}
          aria-describedby={hintId}
          onCheckedChange={(value) => onChange(value === true)}
        />
        <Label htmlFor={id}>{label}</Label>
      </div>
      <p id={hintId} className="text-sm text-muted-foreground">{hint}</p>
    </div>
  )
}

type DraftTextAreaProps = {
  readonly label: string
  readonly value: string
  readonly onChange: (value: string) => void
  readonly disabled: boolean
  readonly hint?: string
  readonly issue?: KbSourceIssue | undefined
  readonly placeholder?: string
  readonly mono?: boolean
}

export function DraftTextArea({ label, value, onChange, disabled, hint, issue, placeholder, mono = false }: DraftTextAreaProps) {
  return (
    <FieldFrame label={label} {...(hint === undefined ? {} : { hint })} issue={issue}>
      {({ id, describedBy, invalid }) => (
        <Textarea
          id={id}
          className={mono ? 'min-h-20 font-mono text-sm' : 'min-h-20'}
          value={value}
          placeholder={placeholder}
          disabled={disabled}
          aria-invalid={invalid}
          aria-describedby={describedBy}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </FieldFrame>
  )
}
