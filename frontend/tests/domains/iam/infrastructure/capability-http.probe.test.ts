import { describe, expect, it, vi } from 'vitest'

import { CapabilityHttpProbe } from '@/domains/iam/infrastructure/capability-http.probe'
import { err, ok } from '@/shared/kernel/result'
import { networkErrorProblem } from '@/shared/http/exception'
import type { Logger } from '@/shared/logging/logger'

const logger: Logger = {
  debug: () => {},
  info: () => {},
  warn: () => {},
  error: () => {},
}

describe('CapabilityHttpProbe', () => {
  it('maps and caches one request for the browsing identity', async () => {
    const request = vi.fn().mockResolvedValue(ok({
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

    expect(request).toHaveBeenCalledOnce()
    expect(request).toHaveBeenCalledWith('/bedrock-chat/admin/_capabilities', {
      credentials: 'include',
      logger,
    })
  })

  it('re-probes after an identity change invalidates the cache', async () => {
    const request = vi.fn()
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
    const request = vi.fn().mockResolvedValue(response)
    const probe = new CapabilityHttpProbe('/admin', { request }, logger)

    await expect(probe.probe()).resolves.toEqual({
      isAdmin: false,
      isAnonymousAdmin: false,
      tokenUsageEnabled: false,
    })
  })
})