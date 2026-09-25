import { toChatBootstrap } from '@/app/bootstrap/bootstrap-config.dto'
import { bootstrapConfigFixture } from './bootstrap-config.fixture'
import { createContainer, type Container } from '@/app/bootstrap/container'
import { isOk } from '@/shared/kernel/result'
import type { Logger } from '@/shared/logging/logger'
import type { CapabilityProbe } from '@/domains/iam/application/ports'

const silentLogger: Logger = {
  debug: () => {},
  info: () => {},
  warn: () => {},
  error: () => {},
}

const adminCapabilityProbe: CapabilityProbe = {
  probe: async () => ({ isAdmin: true, isAnonymousAdmin: false, tokenUsageEnabled: true }),
  invalidate: () => {},
}

// STD-002 §4: a container built entirely from fakes, so anything needing one — the router, a
// guard, a use case — is exercisable without `fetch`, a DOM or `jest.mock`.
export const fakeContainer = (overrides: Partial<Container> = {}): Container => {
  const bootstrap = toChatBootstrap(bootstrapConfigFixture)

  if (!isOk(bootstrap)) {
    throw new Error('bootstrapConfigFixture must parse for the fake container to be meaningful')
  }

  return {
    ...createContainer(bootstrap.value, { logger: silentLogger, capabilityProbe: adminCapabilityProbe }),
    ...overrides,
  }
}
