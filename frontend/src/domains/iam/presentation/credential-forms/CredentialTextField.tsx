import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { IAM_COPY } from '@/shared/copy/iam'

import type { CredentialField } from '@/domains/iam/domain/credential'
import {
  fieldDomId,
  type CredentialFormProps,
} from '@/domains/iam/presentation/credential-forms/credential-form'

export type CredentialTextFieldProps = CredentialFormProps & {
  readonly field: CredentialField
  readonly secret?: boolean | undefined
  readonly multiline?: boolean | undefined
}

// FR-IAM-002 / FR-IAM-004: one field, its own `type`, and its own `aria-invalid` +
// `aria-describedby`-linked message. Typing clears only this field's error.
export function CredentialTextField({
  field,
  secret = false,
  multiline = false,
  idPrefix,
  draft,
  errors,
  disabled,
  onFieldChange,
}: CredentialTextFieldProps) {
  const id = fieldDomId(idPrefix, field)
  const errorId = `${id}-error`
  const message = errors[field]
  const copy = IAM_COPY.fields[field]

  const shared = {
    id,
    name: field,
    value: draft[field] ?? '',
    placeholder: copy.placeholder,
    autoComplete: 'off' as const,
    disabled,
    onChange: (event: { target: { value: string } }) => {
      onFieldChange(field, event.target.value)
    },
    ...(message === undefined ? {} : { 'aria-invalid': true, 'aria-describedby': errorId }),
  }

  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>{copy.label}</Label>
      {multiline ? (
        <Textarea {...shared} rows={4} className="font-mono" />
      ) : (
        <Input {...shared} type={secret ? 'password' /* nosemgrep: required password input type */ : 'text'} />
      )}
      {message === undefined ? null : (
        <p id={errorId} role="alert" className="text-sm text-destructive">
          {message}
        </p>
      )}
    </div>
  )
}
