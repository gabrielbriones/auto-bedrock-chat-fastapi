import { describe, expect, it, jest } from '@jest/globals'

import { ok, type Result } from '@/shared/kernel/result'
import type { Problem } from '@/shared/http/exception'
import type { HttpRequestInit } from '@/shared/http/http-client'

import { SsoHttpGateway } from '@/domains/iam/infrastructure/sso-http.gateway'

// The port's `request` is generic in its response type, which jest-mock's `Mock<T>` erases, so a
// mock typed as the port itself cannot be handed back to it. A fake answering `any` response is
// the honest equivalent: it replies with whatever the spec scripted, whatever the caller asked for.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type ScriptedRequest = (input: string, init?: HttpRequestInit) => Promise<Result<any, Problem>>
const requestMock = () => jest.fn<ScriptedRequest>()

describe('SsoHttpGateway', () => {
  it.each([
    [
      '/bedrock-chat/auth/sso/login',
      '/bedrock-chat/ui/?prompt=workload-analysis&JOB_ID=123',
      '/bedrock-chat/auth/sso/login?next=%2Fbedrock-chat%2Fui%2F%3Fprompt%3Dworkload-analysis%26JOB_ID%3D123',
    ],
    ['/login?provider=corp', '/bedrock-chat/ui/', '/login?provider=corp&next=%2Fbedrock-chat%2Fui%2F'],
  ])(
    'preserves the complete deep link in the login next parameter without replacing existing login parameters (%s)',
    (loginUrl, next, expected) => {
      const assign = jest.fn()
      const gateway = new SsoHttpGateway(loginUrl, '/logout', { request: requestMock() }, { assign })

      gateway.beginLogin(next)

      expect(assign).toHaveBeenCalledExactlyOnceWith(expected)
    },
  )

  it.each(['/login?', '/login?provider=corp&'])('uses an existing query separator in %s', (loginUrl) => {
    const assign = jest.fn()
    const gateway = new SsoHttpGateway(
      loginUrl,
      '/logout',
      { request: requestMock() },
      { assign },
    )

    gateway.beginLogin('/bedrock-chat/ui/')

    expect(assign).toHaveBeenCalledExactlyOnceWith(`${loginUrl}next=%2Fbedrock-chat%2Fui%2F`)
  })

  it('posts logout with cookies and returns the HTTP result', async () => {
    const response = ok(undefined)
    const request = requestMock().mockResolvedValue(response)
    const gateway = new SsoHttpGateway(
      '/login',
      '/bedrock-chat/auth/sso/logout',
      { request },
      { assign: jest.fn() },
    )

    await expect(gateway.logout()).resolves.toBe(response)
    expect(request).toHaveBeenCalledExactlyOnceWith('/bedrock-chat/auth/sso/logout', {
      method: 'POST',
      credentials: 'include',
    })
  })
})