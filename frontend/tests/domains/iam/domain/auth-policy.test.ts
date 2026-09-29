import { describe, expect, it } from '@jest/globals'

import {
  initialCredentialKind,
  isKindSelectorHidden,
  toAuthPolicy,
  type AuthPolicySource,
} from '@/domains/iam/domain/auth-policy'

const source = (overrides: Partial<AuthPolicySource> = {}): AuthPolicySource => ({
  authEnabled: true,
  requireAuth: false,
  supportedAuthTypes: ['bearer_token', 'basic_auth', 'sso'],
  defaultAuthType: 'basic_auth',
  ssoEnabled: true,
  ssoLoginUrl: '/chat/auth/sso/login',
  ...overrides,
})

describe('the auth policy', () => {
  // FR-IAM-001
  it('drops sso from the supported kinds when sso is disabled', () => {
    const policy = toAuthPolicy(source({ ssoEnabled: false }))

    expect(policy.supportedKinds).toEqual(['bearer_token', 'basic_auth'])
  })

  it('canonicalises the backend oauth2 alias and drops unknown wire values', () => {
    const policy = toAuthPolicy(source({ supportedAuthTypes: ['oauth2', 'mtls', 'oauth2'] }))

    expect(policy.supportedKinds).toEqual(['oauth2_client_credentials'])
  })

  // FR-IAM-001b
  it('keeps the default kind only when it survives filtering', () => {
    expect(toAuthPolicy(source()).defaultKind).toBe('basic_auth')
    expect(toAuthPolicy(source({ defaultAuthType: 'sso', ssoEnabled: false })).defaultKind).toBeNull()
    expect(toAuthPolicy(source({ defaultAuthType: 'nonsense' })).defaultKind).toBeNull()
  })

  // FR-IAM-001a
  it('auto-selects and hides the selector when a single kind remains', () => {
    const policy = toAuthPolicy(
      source({ supportedAuthTypes: ['sso', 'bearer_token'], ssoEnabled: false }),
    )

    expect(isKindSelectorHidden(policy)).toBe(true)
    expect(initialCredentialKind(policy)).toBe('bearer_token')
  })

  it('falls back to the default kind when several remain', () => {
    const policy = toAuthPolicy(source())

    expect(isKindSelectorHidden(policy)).toBe(false)
    expect(initialCredentialKind(policy)).toBe('basic_auth')
  })

  it('selects nothing when several kinds remain and none is the default', () => {
    expect(initialCredentialKind(toAuthPolicy(source({ defaultAuthType: '' })))).toBeNull()
  })
})
