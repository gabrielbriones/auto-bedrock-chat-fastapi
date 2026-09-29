import type { AuthPolicy } from '@/domains/iam/domain/auth-policy'
import { toCredentialKind, type CredentialKind } from '@/domains/iam/domain/credential'

export type AuthMethodDeepLink =
  | { readonly kind: 'auto-redirect' }
  | { readonly kind: 'preselect'; readonly credentialKind: CredentialKind }
  | { readonly kind: 'ignore' }

// FR-IAM-007. Security-critical: this reads exactly one parameter and returns either a kind or
// nothing. No credential value is ever accepted from the URL, so there is no branch that could
// carry one.
export const applyAuthMethodDeepLink = (
  rawAuthMethod: string | null | undefined,
  policy: AuthPolicy,
): AuthMethodDeepLink => {
  if (rawAuthMethod === null || rawAuthMethod === undefined || rawAuthMethod === '') {
    return { kind: 'ignore' }
  }

  const credentialKind = toCredentialKind(rawAuthMethod)

  if (credentialKind === null || !policy.supportedKinds.includes(credentialKind)) {
    return { kind: 'ignore' }
  }

  // `supportedKinds` already excludes `sso` when it is disabled, so reaching it here means enabled.
  return credentialKind === 'sso' ? { kind: 'auto-redirect' } : { kind: 'preselect', credentialKind }
}
