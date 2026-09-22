import { describe, expect, it, vi } from 'vitest'

import { ok } from '@/shared/kernel/result'

import { SsoHttpGateway } from '@/domains/iam/infrastructure/sso-http.gateway'

describe('SsoHttpGateway', () => {
  it('preserves the complete deep link in the login next parameter', () => {
    const assign = vi.fn()
    const gateway = new SsoHttpGateway(
      '/bedrock-chat/auth/sso/login',
      '/bedrock-chat/auth/sso/logout',
      { request: vi.fn() },
      { assign },
    )

    gateway.beginLogin('/bedrock-chat/ui/?prompt=workload-analysis&JOB_ID=123')

    expect(assign).toHaveBeenCalledExactlyOnceWith(
      '/bedrock-chat/auth/sso/login?next=%2Fchat%2Fui%2F%3Fprompt%3Dworkload-analysis%26JOB_ID%3D123',
    )
  })

  it('appends next without replacing existing login parameters', () => {
    const assign = vi.fn()
    const gateway = new SsoHttpGateway(
      '/login?provider=corp',
      '/logout',
      { request: vi.fn() },
      { assign },
    )

    gateway.beginLogin('/bedrock-chat/ui/')

    expect(assign).toHaveBeenCalledExactlyOnceWith('/login?provider=corp&next=%2Fchat%2Fui%2F')
  })

  it.each(['/login?', '/login?provider=corp&'])('uses an existing query separator in %s', (loginUrl) => {
    const assign = vi.fn()
    const gateway = new SsoHttpGateway(
      loginUrl,
      '/logout',
      { request: vi.fn() },
      { assign },
    )

    gateway.beginLogin('/bedrock-chat/ui/')

    expect(assign).toHaveBeenCalledExactlyOnceWith(`${loginUrl}next=%2Fchat%2Fui%2F`)
  })

  it('posts logout with cookies and returns the HTTP result', async () => {
    const response = ok(undefined)
    const request = vi.fn().mockResolvedValue(response)
    const gateway = new SsoHttpGateway(
      '/login',
      '/bedrock-chat/auth/sso/logout',
      { request },
      { assign: vi.fn() },
    )

    await expect(gateway.logout()).resolves.toBe(response)
    expect(request).toHaveBeenCalledExactlyOnceWith('/bedrock-chat/auth/sso/logout', {
      method: 'POST',
      credentials: 'include',
    })
  })
})