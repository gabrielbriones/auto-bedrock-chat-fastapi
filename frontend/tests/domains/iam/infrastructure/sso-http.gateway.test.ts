import { describe, expect, it, jest } from '@jest/globals'

import { ok } from '@/shared/kernel/result'

import { SsoHttpGateway } from '@/domains/iam/infrastructure/sso-http.gateway'

describe('SsoHttpGateway', () => {
  it('preserves the complete deep link in the login next parameter', () => {
    const assign = jest.fn()
    const gateway = new SsoHttpGateway(
      '/bedrock-chat/auth/sso/login',
      '/bedrock-chat/auth/sso/logout',
      { request: jest.fn() },
      { assign },
    )

    gateway.beginLogin('/bedrock-chat/ui/?prompt=workload-analysis&JOB_ID=123')

    expect(assign).toHaveBeenCalledExactlyOnceWith(
      '/bedrock-chat/auth/sso/login?next=%2Fbedrock-chat%2Fui%2F%3Fprompt%3Dworkload-analysis%26JOB_ID%3D123',
    )
  })

  it('appends next without replacing existing login parameters', () => {
    const assign = jest.fn()
    const gateway = new SsoHttpGateway(
      '/login?provider=corp',
      '/logout',
      { request: jest.fn() },
      { assign },
    )

    gateway.beginLogin('/bedrock-chat/ui/')

    expect(assign).toHaveBeenCalledExactlyOnceWith('/login?provider=corp&next=%2Fbedrock-chat%2Fui%2F')
  })

  it.each(['/login?', '/login?provider=corp&'])('uses an existing query separator in %s', (loginUrl) => {
    const assign = jest.fn()
    const gateway = new SsoHttpGateway(
      loginUrl,
      '/logout',
      { request: jest.fn() },
      { assign },
    )

    gateway.beginLogin('/bedrock-chat/ui/')

    expect(assign).toHaveBeenCalledExactlyOnceWith(`${loginUrl}next=%2Fbedrock-chat%2Fui%2F`)
  })

  it('posts logout with cookies and returns the HTTP result', async () => {
    const response = ok(undefined)
    const request = jest.fn().mockResolvedValue(response)
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