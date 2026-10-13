import { useState } from 'react'

import type { Credential, CredentialField, CredentialKind, FieldErrors } from '@/domains/iam/domain/credential'
import { emptyDraft, validateCredential } from '@/domains/iam/domain/credential-policy'
import { isErr, type Result } from '@/shared/kernel/result'

/**
 * The in-progress credential the dialog is editing: which kind is selected, the values typed so
 * far, and the per-field errors of the last validation (FR-IAM-003).
 */
export function useCredentialDraft(initialKind: CredentialKind | null) {
  const [kind, setKind] = useState<CredentialKind | null>(initialKind)
  const [draft, setDraft] = useState<Readonly<Record<string, string>>>(() =>
    initialKind === null ? {} : emptyDraft(initialKind),
  )
  const [errors, setErrors] = useState<FieldErrors>({})

  // FR-IAM-003: the draft is replaced wholesale rather than merged, so no value from the previous
  // kind survives — and each kind renders a different component, so none is left in the DOM either.
  const changeKind = (next: CredentialKind) => {
    setKind(next)
    setDraft(emptyDraft(next))
    setErrors({})
  }

  const changeField = (field: CredentialField, value: string) => {
    setDraft((current) => ({ ...current, [field]: value }))
    setErrors((current) => {
      const remaining = { ...current }
      delete remaining[field]
      return remaining
    })
  }

  /** Validates the draft and publishes the outcome as field errors. `null` if no kind is chosen. */
  const validate = (): Result<Credential, FieldErrors> | null => {
    if (kind === null) {
      return null
    }

    const result = validateCredential(kind, draft)
    setErrors(isErr(result) ? result.error : {})

    return result
  }

  return { kind, draft, errors, changeKind, changeField, validate }
}

export type CredentialDraft = ReturnType<typeof useCredentialDraft>
