import type { CredentialField, FieldErrors } from '@/domains/iam/domain/credential'

export type CredentialFormProps = {
  readonly idPrefix: string
  readonly draft: Readonly<Record<string, string>>
  readonly errors: FieldErrors
  readonly disabled: boolean
  readonly onFieldChange: (field: CredentialField, value: string) => void
}

export const fieldDomId = (idPrefix: string, field: CredentialField): string =>
  `${idPrefix}-${field}`
