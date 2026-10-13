import { describe, expect, it, jest } from '@jest/globals'

import { IAM_COPY } from '@/shared/copy/iam'
import type { AuthEvent } from '@/domains/iam/application/ports'
import { IdentityStore } from '@/domains/iam/application/identity.store'
import { toAuthPolicy } from '@/domains/iam/domain/auth-policy'

import { notifyOnSessionExpiry } from '@/app/bootstrap/session-expiry-notice'

const setup = () => {
  let emit: (event: AuthEvent) => void = () => {}
  const policy = toAuthPolicy({
    authEnabled: true,
    requireAuth: false,
    supportedAuthTypes: ['sso'],
    defaultAuthType: 'sso',
    ssoEnabled: true,
    authExpirationBehaviour: 'none',
    ssoLoginUrl: '/chat/auth/sso/login',
  })
  const identity = new IdentityStore({
    policy,
    authGateway: {
      authenticate: () => 'sent',
      logout: () => 'sent',
      refreshSessionToken: () => 'sent',
      onAuthEvent: (listener) => {
        emit = listener
        return () => {}
      },
    },
    ssoGateway: {
      beginLogin: jest.fn(),
      logout: async () => ({ kind: 'ok', value: undefined }) as const,
      refresh: async () => 'renewed' as const,
    },
    connection: { onStateChange: () => () => {} },
    renewalScheduler: { every: () => () => {} },
    renewalIntervalMs: 1000,
    initial: { policy, ssoAuthenticated: true, ssoUserDisplay: null },
  })
  const notifications = { success: jest.fn(), error: jest.fn(), info: jest.fn(), warning: jest.fn() }
  notifyOnSessionExpiry(identity, notifications)

  return { notifications, emit: (event: AuthEvent) => emit(event) }
}

describe('notifyOnSessionExpiry', () => {
  it('tells the user once, with a persistent toast, that their session expired', () => {
    const { notifications, emit } = setup()

    emit({ kind: 'expired', message: '' })
    emit({ kind: 'expired', message: 'again' })

    expect(notifications.warning).toHaveBeenCalledExactlyOnceWith(IAM_COPY.sessionExpired.title, {
      description: IAM_COPY.sessionExpired.description,
      durationMs: 0,
    })
  })

  it('stays quiet for a normal logout', () => {
    const { notifications, emit } = setup()

    emit({ kind: 'logged-out', message: 'bye' })

    expect(notifications.warning).not.toHaveBeenCalled()
  })
})
