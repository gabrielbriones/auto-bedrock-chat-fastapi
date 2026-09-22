import { IAM_COPY } from '@/shared/copy/iam'
import { err, ok, type Result } from '@/shared/kernel/result'

import {
  DEFAULT_API_KEY_HEADER,
  type Credential,
  type CredentialDraft,
  type CredentialField,
  type CredentialKind,
  type FieldErrors,
} from '@/domains/iam/domain/credential'

// Declaration order doubles as the tab order, so FR-IAM-004's "first invalid field" is the first
// entry here that carries an error.
export const CREDENTIAL_FIELDS: Readonly<
  Record<CredentialKind, readonly CredentialField[]>
> = {
  bearer_token: ['token'],
  basic_auth: ['username', 'password'],
  api_key: ['apiKey', 'header'],
  oauth2_client_credentials: ['clientId', 'clientSecret', 'tokenUrl', 'scope'],
  custom: ['headers'],
  sso: [],
}

export const firstInvalidField = (
  kind: CredentialKind,
  errors: FieldErrors,
): CredentialField | null =>
  CREDENTIAL_FIELDS[kind].find((field) => errors[field] !== undefined) ?? null

// FR-IAM-003: switching kind starts from this, so no value can survive the switch.
export const emptyDraft = (kind: CredentialKind): Readonly<Record<string, string>> =>
  kind === 'api_key' ? { header: DEFAULT_API_KEY_HEADER } : {}

const trimmedString = (draft: CredentialDraft, field: string): string => {
  const value = draft[field]
  return typeof value === 'string' ? value.trim() : ''
}

const requiredErrors = (
  values: Readonly<Record<string, string>>,
): FieldErrors => Object.fromEntries(
  Object.entries(values)
    .filter(([, value]) => value.length === 0)
    .map(([field]) => [field, IAM_COPY.validation.required]),
) as FieldErrors

const hasErrors = (errors: FieldErrors): boolean => Object.keys(errors).length > 0

const validateCustomHeaders = (
  draft: CredentialDraft,
): Result<Credential, FieldErrors> => {
  const source = draft.headers
  if (typeof source !== 'string' || source.trim().length === 0) {
    return err({ headers: IAM_COPY.validation.required })
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(source)
  } catch {
    return err({ headers: IAM_COPY.validation.invalidJson })
  }

  const isHeaderMap = parsed !== null
    && typeof parsed === 'object'
    && !Array.isArray(parsed)
    && Object.values(parsed).every((value) => typeof value === 'string')

  if (!isHeaderMap) {
    return err({ headers: IAM_COPY.validation.invalidHeaderMap })
  }

  return ok({
    kind: 'custom',
    headers: parsed as Record<string, string>,
  })
}

export const validateCredential = (
  kind: CredentialKind,
  draft: CredentialDraft,
): Result<Credential, FieldErrors> => {
  switch (kind) {
    case 'bearer_token': {
      const token = trimmedString(draft, 'token')
      return token.length > 0
        ? ok({ kind, token })
        : err({ token: IAM_COPY.validation.required })
    }
    case 'basic_auth': {
      const values = {
        username: trimmedString(draft, 'username'),
        password: trimmedString(draft, 'password'),
      }
      const errors = requiredErrors(values)
      return hasErrors(errors) ? err(errors) : ok({ kind, ...values })
    }
    case 'api_key': {
      const values = {
        apiKey: trimmedString(draft, 'apiKey'),
        header: trimmedString(draft, 'header'),
      }
      const errors = requiredErrors(values)
      return hasErrors(errors) ? err(errors) : ok({ kind, ...values })
    }
    case 'oauth2_client_credentials': {
      const values = {
        clientId: trimmedString(draft, 'clientId'),
        clientSecret: trimmedString(draft, 'clientSecret'),
        tokenUrl: trimmedString(draft, 'tokenUrl'),
      }
      const errors = requiredErrors(values)
      if (hasErrors(errors)) {
        return err(errors)
      }

      const scope = trimmedString(draft, 'scope')
      return ok(scope.length > 0 ? { kind, ...values, scope } : { kind, ...values })
    }
    case 'custom':
      return validateCustomHeaders(draft)
    case 'sso':
      return ok({ kind })
  }
}