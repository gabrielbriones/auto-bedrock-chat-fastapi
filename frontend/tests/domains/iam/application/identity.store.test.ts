import { describe, expect, it, jest } from '@jest/globals'

import { SystemClock } from '@/shared/kernel/instant'
import { ConsoleLogger } from '@/shared/logging/console-logger'
import { FakeChatSocket } from '../../../shared/ws/fake-chat-socket'
import { MessageBus } from '@/shared/ws/message-bus'
import { SocketClient } from '@/shared/ws/socket-client'
import type { ConnectionState, SendResult, Unsubscribe } from '@/shared/ws/socket-client'

import { toAuthPolicy, type AuthPolicySource } from '@/domains/iam/domain/auth-policy'
import type { Credential } from '@/domains/iam/domain/credential'
import type { AuthEvent, AuthGateway, SsoGateway } from '@/domains/iam/application/ports'
import { WsAuthGateway } from '@/domains/iam/infrastructure/ws-auth.gateway'
import { IdentityStore, type ConnectionSource } from '@/domains/iam/application/identity.store'

const policySource = (overrides: Partial<AuthPolicySource> = {}): AuthPolicySource => ({
  authEnabled: true,
  requireAuth: false,
  supportedAuthTypes: ['bearer_token', 'sso'],
  defaultAuthType: 'bearer_token',
  ssoEnabled: true,
  ssoLoginUrl: '/chat/auth/sso/login',
  ...overrides,
})

const bearer: Credential = { kind: 'bearer_token', token: 'jwt' }

const openState: ConnectionState = { status: 'open', attempt: 0, nextRetryAt: null }
const closedState: ConnectionState = { status: 'closed', attempt: 0, nextRetryAt: null }

const setup = (
  source: Partial<AuthPolicySource> = {},
  initial: { ssoAuthenticated?: boolean; ssoUserDisplay?: string | null } = {},
) => {
  const authenticate = jest.fn<(credential: Credential) => SendResult>(() => 'sent')
  let emitAuthEvent: (event: AuthEvent) => void = () => {}
  let emitConnectionState: (state: ConnectionState) => void = () => {}

  const authGateway: AuthGateway = {
    authenticate,
    logout: jest.fn<() => SendResult>(() => 'sent'),
    onAuthEvent: (listener): Unsubscribe => {
      emitAuthEvent = listener
      return () => {}
    },
  }

  const ssoGateway: SsoGateway = {
    beginLogin: jest.fn(),
    logout: jest.fn(async () => ({ kind: 'ok', value: undefined }) as const),
  }

  const connection: ConnectionSource = {
    onStateChange: (listener): Unsubscribe => {
      emitConnectionState = listener
      return () => {}
    },
  }

  const policy = toAuthPolicy(policySource(source))
  const store = new IdentityStore({
    policy,
    authGateway,
    ssoGateway,
    connection,
    initial: {
      policy,
      ssoAuthenticated: initial.ssoAuthenticated ?? false,
      ssoUserDisplay: initial.ssoUserDisplay ?? null,
    },
  })

  return {
    store,
    authenticate,
    ssoGateway,
    authEvent: (event: AuthEvent) => {
      emitAuthEvent(event)
    },
    connectionState: (state: ConnectionState) => {
      emitConnectionState(state)
    },
  }
}

describe('the identity store', () => {
  it('opens the dialog when auth is enabled and no SSO session exists', () => {
    expect(setup().store.getSnapshot().dialogOpen).toBe(true)
  })

  // FR-IAM-017
  it('shows no dialog when bootstrap reports an established SSO session', () => {
    const { store } = setup({}, { ssoAuthenticated: true, ssoUserDisplay: 'Ada Lovelace' })
    const session = store.getSnapshot()

    expect(session.dialogOpen).toBe(false)
    expect(session.status).toBe('authenticated')
    expect(session.principal.displayName).toBe('Ada Lovelace')
  })

  // FR-IAM-014
  it('closes the dialog and records the identity on auth_configured', () => {
    const { store, authEvent } = setup()

    store.submit(bearer)
    expect(store.getSnapshot().status).toBe('authenticating')

    authEvent({ kind: 'configured', authKind: 'bearer_token', displayName: 'Ada', message: 'ok' })
    const session = store.getSnapshot()

    expect(session.status).toBe('authenticated')
    expect(session.dialogOpen).toBe(false)
    expect(session.principal.mode).toBe('tool-auth')
    expect(session.notice?.text).toBe('🔐 ok')
  })

  // FR-IAM-015
  it('re-opens the dialog and blocks input on auth_failed when auth is required', () => {
    const { store, authEvent } = setup({ requireAuth: true })

    store.submit(bearer)
    authEvent({ kind: 'failed', authKind: 'bearer_token', message: 'bad token' })
    const session = store.getSnapshot()

    expect(session.status).toBe('failed')
    expect(session.dialogOpen).toBe(true)
    expect(session.inputEnabled).toBe(false)
    expect(session.notice?.text).toBe('❌ Authentication failed: bad token')
  })

  // FR-IAM-010 — the requirement this whole slice exists for.
  it('re-authenticates the new socket automatically after a reconnect', () => {
    const { store, authenticate, authEvent, connectionState } = setup()

    connectionState(openState)
    store.submit(bearer)
    authEvent({ kind: 'configured', authKind: 'bearer_token', message: 'ok' })
    expect(authenticate).toHaveBeenCalledTimes(1)

    connectionState(closedState)
    connectionState(openState)

    expect(authenticate).toHaveBeenCalledTimes(2)
    expect(authenticate).toHaveBeenLastCalledWith(bearer)
    expect(store.getSnapshot().status).toBe('authenticating')
    expect(store.getSnapshot().dialogOpen).toBe(false)
  })

  it('does not re-authenticate a reconnect when no credential was ever accepted', () => {
    const { authenticate, connectionState, store } = setup()

    connectionState(openState)

    expect(authenticate).not.toHaveBeenCalled()
    expect(store.getSnapshot().status).toBe('unauthenticated')
  })

  // FR-IAM-015: a rejected credential must not be replayed on the next socket.
  it('discards the credential after a failure, so a reconnect does not replay it', () => {
    const { store, authenticate, authEvent, connectionState } = setup()

    store.submit(bearer)
    authEvent({ kind: 'failed', authKind: 'bearer_token', message: 'bad token' })
    connectionState(openState)

    expect(authenticate).toHaveBeenCalledTimes(1)
  })

  // FR-IAM-009
  it('clears identity and pre-selects sso on expiry', () => {
    const { store, authEvent } = setup()

    store.submit(bearer)
    authEvent({ kind: 'configured', authKind: 'bearer_token', message: 'ok' })
    authEvent({ kind: 'expired', message: '' })
    const session = store.getSnapshot()

    expect(session.status).toBe('expired')
    expect(session.principal.mode).toBe('anonymous')
    expect(session.preselectedKind).toBe('sso')
    expect(session.notice?.text).toBe('⏰ Session expired. Please log in again.')
    expect(session.inputEnabled).toBe(false)
  })

  // FR-IAM-006
  it('logs out through the SSO gateway only while in sso mode', async () => {
    const { store, authEvent, ssoGateway } = setup()

    authEvent({ kind: 'configured', authKind: 'sso', message: 'ok' })
    await store.logout()

    expect(ssoGateway.logout).toHaveBeenCalledTimes(1)
  })

  // FR-IAM-008
  it('ignores a skip when authentication is required', () => {
    const { store } = setup({ requireAuth: true })

    store.skip()

    expect(store.getSnapshot().dialogOpen).toBe(true)
  })

  it('closes the dialog on skip when authentication is optional', () => {
    const { store } = setup({ requireAuth: false })

    store.skip()

    expect(store.getSnapshot().dialogOpen).toBe(false)
    expect(store.getSnapshot().inputEnabled).toBe(true)
  })

  // FR-IAM-010b: the frame never left, so the submit control must come back.
  it('restores the control and keeps the credential when the frame is dropped', () => {
    const { store, authenticate, connectionState } = setup()
    authenticate.mockReturnValueOnce('dropped-closed')

    store.submit(bearer)

    expect(store.getSnapshot().status).toBe('unauthenticated')
    expect(store.getSnapshot().dialogOpen).toBe(true)

    connectionState(openState)

    expect(authenticate).toHaveBeenCalledTimes(2)
  })

  // FR-IAM-007
  it('pre-selects a kind from ?auth_method without redirecting', () => {
    const { store, ssoGateway } = setup()

    store.applyDeepLink('bearer_token', '/bedrock-chat/ui/')

    expect(store.getSnapshot().preselectedKind).toBe('bearer_token')
    expect(ssoGateway.beginLogin).not.toHaveBeenCalled()
  })

  it('auto-redirects for ?auth_method=sso, preserving the deep link', () => {
    const { store, ssoGateway } = setup()

    store.applyDeepLink('sso', '/bedrock-chat/ui/?prompt=workload-analysis&JOB_ID=123')

    expect(ssoGateway.beginLogin).toHaveBeenCalledWith('/bedrock-chat/ui/?prompt=workload-analysis&JOB_ID=123')
  })

  it('ignores an unsupported or unknown auth_method', () => {
    const { store, ssoGateway } = setup({ supportedAuthTypes: ['bearer_token'], ssoEnabled: false })

    store.applyDeepLink('sso', '/bedrock-chat/ui/')
    store.applyDeepLink('nonsense', '/bedrock-chat/ui/')

    expect(store.getSnapshot().preselectedKind).toBeNull()
    expect(ssoGateway.beginLogin).not.toHaveBeenCalled()
  })

  it('ignores a deep link once an SSO cookie has already authenticated the session', () => {
    const { store, ssoGateway } = setup({}, { ssoAuthenticated: true })

    store.applyDeepLink('sso', '/bedrock-chat/ui/')

    expect(ssoGateway.beginLogin).not.toHaveBeenCalled()
  })
})

// FR-IAM-010a. Wired to the real transport rather than a fake gateway, because the property under
// test is exactly the one a fake would assume away: the legacy client opened a fresh socket per
// attempt, so a rejected credential lost the session it was being checked against.
describe('retrying a rejected credential', () => {
  it('reuses the open socket instead of reconnecting', () => {
    const socket = new FakeChatSocket()
    const socketFactory = jest.fn(() => socket)
    const client = new SocketClient({
      clock: new SystemClock(),
      connectivity: { status: () => 'online', subscribe: () => () => {} },
      socketFactory,
      url: 'wss://analyzer.test/bedrock-chat/ws',
    })
    const messageBus = new MessageBus(new ConsoleLogger())
    client.onFrame((frame) => {
      messageBus.receive(frame)
    })

    const policy = toAuthPolicy(policySource({ requireAuth: true }))
    const authGateway = new WsAuthGateway(client, messageBus, new ConsoleLogger(), 'https://analyzer.test')
    const store = new IdentityStore({
      policy,
      authGateway,
      ssoGateway: { beginLogin: jest.fn(), logout: jest.fn(async () => ({ kind: 'ok', value: undefined }) as const) },
      connection: client,
      initial: { policy, ssoAuthenticated: false, ssoUserDisplay: null },
    })

    client.connect()
    socket.onopen?.({} as Event)

    store.submit({ kind: 'bearer_token', token: 'wrong' })
    socket.onmessage?.({
      data: JSON.stringify({
        type: 'auth_failed',
        timestamp: '2026-08-25T00:00:00Z',
        auth_type: 'bearer_token',
        message: 'Invalid token',
      }),
    } as MessageEvent<unknown>)

    expect(store.getSnapshot().dialogOpen).toBe(true)

    store.submit({ kind: 'bearer_token', token: 'right' })

    expect(socketFactory).toHaveBeenCalledTimes(1)
    expect(
      socket.sent.map((frame) => (JSON.parse(frame) as { token: string }).token),
    ).toEqual(['wrong', 'right'])

    store.dispose()
    client.dispose()
  })
})
