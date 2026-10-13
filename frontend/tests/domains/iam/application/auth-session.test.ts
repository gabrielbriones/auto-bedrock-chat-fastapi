import { describe, expect, it } from '@jest/globals'

import { toAuthPolicy, type AuthPolicySource } from '@/domains/iam/domain/auth-policy'
import type { SessionAuthState } from '@/domains/iam/domain/auth-policy'
import type { Principal } from '@/domains/iam/domain/principal'
import { initialAuthSession, shouldRenewSession, type AuthSession } from '@/domains/iam/application/auth-session'

const policy = (overrides: Partial<AuthPolicySource> = {}) =>
  toAuthPolicy({
    authEnabled: true,
    requireAuth: false,
    supportedAuthTypes: ['sso'],
    defaultAuthType: 'sso',
    ssoEnabled: true,
    authExpirationBehaviour: 'both',
    ssoLoginUrl: '/chat/auth/sso/login',
    ...overrides,
  })

const session = (status: SessionAuthState, mode: Principal['mode']): AuthSession => {
  const base = initialAuthSession({ policy: policy(), ssoAuthenticated: true, ssoUserDisplay: null })
  return { ...base, status, principal: { ...base.principal, mode } }
}

describe('shouldRenewSession', () => {
  it.each(['proactive', 'reactive', 'both'] as const)('renews an SSO session with %s', (behaviour) => {
    expect(shouldRenewSession(policy({ authExpirationBehaviour: behaviour }), session('authenticated', 'sso'))).toBe(
      true,
    )
  })

  // A socket reset drops status to unauthenticated/authenticating but keeps the SSO principal.
  it.each<SessionAuthState>(['unauthenticated', 'authenticating'])('keeps renewing while %s', (status) => {
    expect(shouldRenewSession(policy(), session(status, 'sso'))).toBe(true)
  })

  it('never renews with behaviour none', () => {
    expect(shouldRenewSession(policy({ authExpirationBehaviour: 'none' }), session('authenticated', 'sso'))).toBe(
      false,
    )
  })

  it('never renews when SSO is disabled', () => {
    expect(shouldRenewSession(policy({ ssoEnabled: false }), session('authenticated', 'sso'))).toBe(false)
  })

  it.each<Principal['mode']>(['anonymous', 'tool-auth'])('never renews a %s principal', (mode) => {
    expect(shouldRenewSession(policy(), session('authenticated', mode))).toBe(false)
  })

  it.each<SessionAuthState>(['failed', 'expired'])('stops renewing once %s', (status) => {
    expect(shouldRenewSession(policy(), session(status, 'sso'))).toBe(false)
  })
})
