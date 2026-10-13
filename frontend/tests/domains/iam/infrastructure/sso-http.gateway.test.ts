import { afterAll, afterEach, beforeAll, describe, expect, it, jest } from '@jest/globals'

import { err, ok, type Result } from '@/shared/kernel/result'
import type { Problem } from '@/shared/http/exception'
import { HttpClient, type HttpRequestInit } from '@/shared/http/http-client'

import { SsoHttpGateway } from '@/domains/iam/infrastructure/sso-http.gateway'
import { chatBase } from '@/app/bootstrap/loadBootstrap'

import { server } from '../../../msw/server'
import {
  ssoRefreshIdpFailureHandler,
  ssoRefreshNetworkErrorHandler,
  ssoRefreshUnauthorizedHandler,
} from '../../../msw/handlers/iam'

// The port's `request` is generic in its response type, which jest-mock's `Mock<T>` erases, so a
// mock typed as the port itself cannot be handed back to it. A fake answering `any` response is
// the honest equivalent: it replies with whatever the spec scripted, whatever the caller asked for.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type ScriptedRequest = (input: string, init?: HttpRequestInit) => Promise<Result<any, Problem>>
const requestMock = () => jest.fn<ScriptedRequest>()

// Deployments mount the chat at `/chat` or the legacy `/bedrock-chat` (VITE_CHAT_BASE).
const CHAT = chatBase()
const LOGIN_URL = `${CHAT}/auth/sso/login`
const LOGOUT_URL = `${CHAT}/auth/sso/logout`
const REFRESH_URL = `${CHAT}/auth/sso/refresh`
const UI = `${CHAT}/ui/`

describe('SsoHttpGateway', () => {
  it.each([
    [
      LOGIN_URL,
      `${UI}?prompt=workload-analysis&JOB_ID=123`,
      `${LOGIN_URL}?next=${encodeURIComponent(`${UI}?prompt=workload-analysis&JOB_ID=123`)}`,
    ],
    ['/login?provider=corp', UI, `/login?provider=corp&next=${encodeURIComponent(UI)}`],
  ])(
    'preserves the complete deep link in the login next parameter without replacing existing login parameters (%s)',
    (loginUrl, next, expected) => {
      const assign = jest.fn()
      const gateway = new SsoHttpGateway(loginUrl, '/logout', '/refresh', { request: requestMock() }, { assign })

      gateway.beginLogin(next)

      expect(assign).toHaveBeenCalledExactlyOnceWith(expected)
    },
  )

  it.each(['/login?', '/login?provider=corp&'])('uses an existing query separator in %s', (loginUrl) => {
    const assign = jest.fn()
    const gateway = new SsoHttpGateway(
      loginUrl,
      '/logout',
      '/refresh',
      { request: requestMock() },
      { assign },
    )

    gateway.beginLogin(UI)

    expect(assign).toHaveBeenCalledExactlyOnceWith(`${loginUrl}next=${encodeURIComponent(UI)}`)
  })

  it('posts logout with cookies and returns the HTTP result', async () => {
    const response = ok(undefined)
    const request = requestMock().mockResolvedValue(response)
    const gateway = new SsoHttpGateway(
      '/login',
      LOGOUT_URL,
      REFRESH_URL,
      { request },
      { assign: jest.fn() },
    )

    await expect(gateway.logout()).resolves.toBe(response)
    expect(request).toHaveBeenCalledExactlyOnceWith(LOGOUT_URL, {
      method: 'POST',
      credentials: 'include',
    })
  })

  it('posts refresh to the published URL with cookies', async () => {
    const request = requestMock().mockResolvedValue(ok({ expires_at: 1 }))
    const gateway = new SsoHttpGateway(
      '/login',
      '/logout',
      REFRESH_URL,
      { request },
      { assign: jest.fn() },
    )

    await expect(gateway.refresh()).resolves.toBe('renewed')
    expect(request).toHaveBeenCalledExactlyOnceWith(REFRESH_URL, {
      method: 'POST',
      credentials: 'include',
    })
  })

  it.each([
    [401, 'expired'],
    [502, 'unavailable'],
  ] as const)('maps a %s refresh to %s without rejecting', async (status, outcome) => {
    const problem = { status, title: 'x' } as unknown as Problem
    const request = requestMock().mockResolvedValue(err(problem))
    const gateway = new SsoHttpGateway('/login', '/logout', '/refresh', { request }, { assign: jest.fn() })

    await expect(gateway.refresh()).resolves.toBe(outcome)
  })
})

describe('SsoHttpGateway against the MSW backend', () => {
  const REFRESH = `http://localhost${REFRESH_URL}`
  const gateway = () => new SsoHttpGateway('/login', '/logout', REFRESH, new HttpClient(), { assign: jest.fn() })
  const requests: Request[] = []

  beforeAll(() => {
    server.listen({ onUnhandledRequest: 'error' })
    server.events.on('request:start', ({ request }) => requests.push(request.clone()))
  })
  afterEach(() => {
    server.resetHandlers()
    requests.length = 0
  })
  afterAll(() => server.close())

  // The session token rides only on the HttpOnly cookie; sending it in the body would echo it back.
  it('posts a body-less refresh to the published URL', async () => {
    await expect(gateway().refresh()).resolves.toBe('renewed')

    expect(requests).toHaveLength(1)
    expect(requests[0]?.method).toBe('POST')
    expect(requests[0]?.url).toBe(REFRESH)
    expect(requests[0]?.headers.get('authorization')).toBeNull()
    await expect(requests[0]?.text()).resolves.toBe('')
  })

  it.each([
    ['a missing cookie (401)', ssoRefreshUnauthorizedHandler, 'expired'],
    ['an IdP failure (502)', ssoRefreshIdpFailureHandler, 'unavailable'],
    ['a network error', ssoRefreshNetworkErrorHandler, 'unavailable'],
  ] as const)('resolves without throwing or retrying on %s', async (_, handler, outcome) => {
    server.use(handler)

    await expect(gateway().refresh()).resolves.toBe(outcome)
    expect(requests).toHaveLength(1)
  })
})
