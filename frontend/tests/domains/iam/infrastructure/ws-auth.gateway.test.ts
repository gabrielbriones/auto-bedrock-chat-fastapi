import { describe, expect, it, jest } from '@jest/globals'

import type { ServerFrame, ServerFrameSubscriber } from '@/shared/ws/message-bus'

import type { AuthEvent } from '@/domains/iam/application/ports'
import type { Credential } from '@/domains/iam/domain/public'
import { WsAuthGateway } from '@/domains/iam/infrastructure/ws-auth.gateway'

const ORIGIN = 'https://analyzer.test'
const credentialValue = 'fixture-credential'

const createHarness = () => {
  const send = jest.fn<(frame: string) => 'sent'>(() => 'sent')
  let subscriber: ServerFrameSubscriber | undefined
  const subscribe = jest.fn((next: ServerFrameSubscriber) => {
    subscriber = next
    return () => {
      subscriber = undefined
    }
  })
  const logger = { debug: jest.fn(), info: jest.fn(), warn: jest.fn(), error: jest.fn() }
  const gateway = new WsAuthGateway({ send }, { subscribe }, logger, ORIGIN)

  return {
    emit(frame: ServerFrame) {
      subscriber?.(frame)
    },
    gateway,
    logger,
    send,
  }
}

describe('WsAuthGateway', () => {
  it.each<Credential>([
    { kind: 'bearer_token', token: 'token' },
    { kind: 'basic_auth', username: 'user', password: credentialValue },
    { kind: 'api_key', apiKey: 'key', header: 'X-API-Key' },
    {
      kind: 'oauth2_client_credentials',
      clientId: 'client',
      clientSecret: credentialValue,
      tokenUrl: 'https://issuer.test/token',
      scope: 'read',
    },
    { kind: 'custom', headers: { 'X-Custom': 'value' } },
    { kind: 'sso' },
  ])('serializes $kind credentials to an auth frame', (credential) => {
    const { gateway, send } = createHarness()

    gateway.authenticate(credential)

    expect(JSON.parse(send.mock.calls[0]?.[0] ?? '')).toMatchObject({
      type: 'auth',
      auth_type: credential.kind,
    })
  })

  it('maps domain fields to backend wire names', () => {
    const { gateway, send } = createHarness()

    gateway.authenticate({
      kind: 'oauth2_client_credentials',
      clientId: 'client',
      clientSecret: credentialValue,
      tokenUrl: 'https://issuer.test/token',
    })
    gateway.authenticate({ kind: 'api_key', apiKey: 'key', header: 'X-API-Key' })
    gateway.authenticate({ kind: 'custom', headers: { 'X-Custom': 'value' } })

    expect(send).toHaveBeenNthCalledWith(1, JSON.stringify({
      type: 'auth',
      auth_type: 'oauth2_client_credentials',
      client_id: 'client',
      client_secret: credentialValue,
      token_url: 'https://issuer.test/token',
    }))
    expect(send).toHaveBeenNthCalledWith(2, JSON.stringify({
      type: 'auth',
      auth_type: 'api_key',
      api_key: 'key',
      api_key_header: 'X-API-Key',
    }))
    expect(send).toHaveBeenNthCalledWith(3, JSON.stringify({
      type: 'auth',
      auth_type: 'custom',
      custom_headers: { 'X-Custom': 'value' },
    }))
  })

  it('sends the logout frame', () => {
    const { gateway, send } = createHarness()

    gateway.logout()

    expect(send).toHaveBeenCalledExactlyOnceWith('{"type":"logout"}')
  })

  it('maps IAM server frames and ignores frames owned by other contexts', () => {
    const { emit, gateway } = createHarness()
    const events: AuthEvent[] = []
    gateway.onAuthEvent((event) => events.push(event))

    emit({
      type: 'auth_configured',
      timestamp: '2026-08-25T00:00:00Z',
      auth_type: 'oauth2',
      display_name: 'Ada',
      message: 'Authenticated',
    })
    emit({
      type: 'auth_expired',
      timestamp: '2026-08-25T00:01:00Z',
      redirect_url: '/login',
      message: 'Expired',
    })
    emit({
      type: 'pong',
      timestamp: '2026-08-25T00:02:00Z',
    })

    expect(events).toEqual([
      {
        kind: 'configured',
        authKind: 'oauth2_client_credentials',
        displayName: 'Ada',
        message: 'Authenticated',
      },
      { kind: 'expired', message: 'Expired', redirectUrl: '/login' },
    ])
  })

  it('maps failed and logout events, including absent optional fields', () => {
    const { emit, gateway } = createHarness()
    const events: AuthEvent[] = []
    gateway.onAuthEvent((event) => events.push(event))

    emit({
      type: 'auth_configured',
      timestamp: '2026-08-25T00:00:00Z',
      auth_type: 'bearer_token',
      message: 'Authenticated',
    })
    emit({
      type: 'auth_failed',
      timestamp: '2026-08-25T00:01:00Z',
      auth_type: 'api_key',
      redirect_url: '/login',
      message: 'Rejected',
    })
    emit({
      type: 'auth_failed',
      timestamp: '2026-08-25T00:02:00Z',
      auth_type: 'basic_auth',
      message: 'Rejected again',
    })
    emit({
      type: 'auth_expired',
      timestamp: '2026-08-25T00:03:00Z',
      message: 'Expired',
    })
    emit({
      type: 'logout_success',
      timestamp: '2026-08-25T00:04:00Z',
      message: 'Logged out',
    })

    expect(events).toEqual([
      { kind: 'configured', authKind: 'bearer_token', message: 'Authenticated' },
      { kind: 'failed', authKind: 'api_key', message: 'Rejected', redirectUrl: '/login' },
      { kind: 'failed', authKind: 'basic_auth', message: 'Rejected again' },
      { kind: 'expired', message: 'Expired' },
      { kind: 'logged-out', message: 'Logged out' },
    ])
  })

  // NFR-SEC-003
  it.each([
    'https://evil.test/steal',
    '//evil.test/steal',
    'javascript:alert(1)',
  ])('drops the off-origin redirect %s', (redirectUrl) => {
    const { emit, gateway } = createHarness()
    const events: AuthEvent[] = []
    gateway.onAuthEvent((event) => events.push(event))

    emit({
      type: 'auth_failed',
      timestamp: '2026-08-25T00:00:00Z',
      auth_type: 'bearer_token',
      redirect_url: redirectUrl,
      message: 'Rejected',
    })
    emit({
      type: 'auth_expired',
      timestamp: '2026-08-25T00:01:00Z',
      redirect_url: redirectUrl,
      message: 'Expired',
    })

    expect(events).toEqual([
      { kind: 'failed', authKind: 'bearer_token', message: 'Rejected' },
      { kind: 'expired', message: 'Expired' },
    ])
  })

  // NFR-SEC-003: same-origin values survive, and a rejection is reported once, not per frame.
  it('keeps same-origin redirects and logs a rejection once', () => {
    const { emit, gateway, logger } = createHarness()
    const events: AuthEvent[] = []
    gateway.onAuthEvent((event) => events.push(event))

    emit({
      type: 'auth_expired',
      timestamp: '2026-08-25T00:00:00Z',
      redirect_url: `${ORIGIN}/bedrock-chat/auth/sso/login`,
      message: 'Expired',
    })
    emit({
      type: 'auth_expired',
      timestamp: '2026-08-25T00:01:00Z',
      redirect_url: 'https://evil.test/one',
      message: 'Expired',
    })
    emit({
      type: 'auth_expired',
      timestamp: '2026-08-25T00:02:00Z',
      redirect_url: 'https://evil.test/two',
      message: 'Expired',
    })

    expect(events[0]).toEqual({
      kind: 'expired',
      message: 'Expired',
      redirectUrl: `${ORIGIN}/bedrock-chat/auth/sso/login`,
    })
    expect(logger.warn).toHaveBeenCalledTimes(1)
    expect(logger.warn.mock.calls[0]?.[0]).toBe('auth_redirect_rejected')
  })

  it('ignores unknown auth types and stops events after unsubscribe', () => {
    const { emit, gateway } = createHarness()
    const listener = jest.fn()
    const unsubscribe = gateway.onAuthEvent(listener)

    emit({
      type: 'auth_configured',
      timestamp: '2026-08-25T00:00:00Z',
      auth_type: 'unknown',
      message: 'Unknown',
    })
    unsubscribe()
    emit({
      type: 'logout_success',
      timestamp: '2026-08-25T00:01:00Z',
      message: 'Logged out',
    })

    expect(listener).not.toHaveBeenCalled()
  })
})