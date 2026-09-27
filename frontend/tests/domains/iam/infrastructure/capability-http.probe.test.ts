import { describe, expect, it, jest } from '@jest/globals'

import { CapabilityHttpProbe } from '@/domains/iam/infrastructure/capability-http.probe'
import { err, ok } from '@/shared/kernel/result'
import { networkErrorProblem, type Problem } from '@/shared/http/exception'
import type { HttpRequestInit } from '@/shared/http/http-client'
import type { Result } from '@/shared/kernel/result'
import type { Logger } from '@/shared/logging/logger'

// The port's `request` is generic in its response type, which jest-mock's `Mock<T>` erases, so a
// mock typed as the port itself cannot be handed back to it. A fake answering `any` response is
// the honest equivalent: it replies with whatever the spec scripted, whatever the caller asked for.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type ScriptedRequest = (input: string, init?: HttpRequestInit) => Promise<Result<any, Problem>>
const requestMock = () => jest.fn<ScriptedRequest>()

const logger: Logger = {
  debug: () => {},
  info: () => {},
  warn: () => {},
  error: () => {},
}

describe('CapabilityHttpProbe', () => {
  it('maps and caches one request for the browsing identity', async () => {
    const request = requestMock().mockResolvedValue(ok({
      is_admin: true,
      anonymous: true,
      token_usage_enabled: false,
    }))
    const probe = new CapabilityHttpProbe('/bedrock-chat/admin', { request }, logger)

    await expect(probe.probe()).resolves.toEqual({
      isAdmin: true,
      isAnonymousAdmin: true,
      tokenUsageEnabled: false,
    })
    await probe.probe()

    expect(request).toHaveBeenCalledTimes(1)
    expect(request).toHaveBeenCalledWith('/bedrock-chat/admin/_capabilities', {
      credentials: 'include',
      logger,
    })
  })

  it('re-probes after an identity change invalidates the cache', async () => {
    const request = requestMock()
      .mockResolvedValueOnce(ok({ is_admin: false, anonymous: false, token_usage_enabled: false }))
      .mockResolvedValueOnce(ok({ is_admin: true, anonymous: false, token_usage_enabled: true }))
    const probe = new CapabilityHttpProbe('/admin', { request }, logger)

    await probe.probe()
    probe.invalidate()

    await expect(probe.probe()).resolves.toMatchObject({ isAdmin: true })
    expect(request).toHaveBeenCalledTimes(2)
  })

  it.each([
    err(networkErrorProblem(new Error('offline'))),
    ok({ is_admin: 'yes' }),
  ])('degrades a failed or malformed response to no capabilities', async (response) => {
    const request = requestMock().mockResolvedValue(response)
    const probe = new CapabilityHttpProbe('/admin', { request }, logger)

    await expect(probe.probe()).resolves.toEqual({
      isAdmin: false,
      isAnonymousAdmin: false,
      tokenUsageEnabled: false,
    })
  })
})