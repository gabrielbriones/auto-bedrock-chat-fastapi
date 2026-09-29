import {
  type CredentialKind,
  toCredentialKind,
} from '@/domains/iam/domain/credential'

// SPEC-010 §1.
export type SessionAuthState =
  | 'unauthenticated'
  | 'authenticating'
  | 'authenticated'
  | 'failed'
  | 'expired'

export type AuthPolicy = {
  readonly enabled: boolean
  readonly required: boolean
  readonly supportedKinds: readonly CredentialKind[]
  readonly defaultKind: CredentialKind | null
  readonly ssoEnabled: boolean
  readonly ssoLoginUrl: string
}

// The subset of the bootstrap payload this context is allowed to see.
export type AuthPolicySource = {
  readonly authEnabled: boolean
  readonly requireAuth: boolean
  readonly supportedAuthTypes: readonly string[]
  readonly defaultAuthType: string
  readonly ssoEnabled: boolean
  readonly ssoLoginUrl: string
}

// FR-IAM-001: unrecognised wire values are dropped, and `sso` only survives when it is enabled.
const toSupportedKinds = (
  authTypes: readonly string[],
  ssoEnabled: boolean,
): readonly CredentialKind[] => {
  const kinds = authTypes
    .map(toCredentialKind)
    .filter((kind): kind is CredentialKind => kind !== null)
    .filter((kind) => kind !== 'sso' || ssoEnabled)

  return [...new Set(kinds)]
}

export const toAuthPolicy = (source: AuthPolicySource): AuthPolicy => {
  const supportedKinds = toSupportedKinds(source.supportedAuthTypes, source.ssoEnabled)
  const defaultKind = toCredentialKind(source.defaultAuthType)

  return {
    enabled: source.authEnabled,
    required: source.requireAuth,
    supportedKinds,
    defaultKind: defaultKind !== null && supportedKinds.includes(defaultKind) ? defaultKind : null,
    ssoEnabled: source.ssoEnabled,
    ssoLoginUrl: source.ssoLoginUrl,
  }
}

// FR-IAM-001a: a lone kind is auto-selected (and its selector hidden). FR-IAM-001b: otherwise the
// configured default wins, and with neither the user must choose.
export const initialCredentialKind = (policy: AuthPolicy): CredentialKind | null =>
  policy.supportedKinds.length === 1 ? (policy.supportedKinds[0] ?? null) : policy.defaultKind

export const isKindSelectorHidden = (policy: AuthPolicy): boolean =>
  policy.supportedKinds.length === 1
