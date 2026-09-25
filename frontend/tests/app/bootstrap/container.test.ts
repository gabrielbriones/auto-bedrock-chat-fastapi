import { describe, expect, it, jest } from '@jest/globals'

import type { Logger } from '@/shared/logging/logger'
import { HttpClient } from '@/shared/http/http-client'

import { bootstrapConfigFixture } from './bootstrap-config.fixture'
import { toChatBootstrap } from '@/app/bootstrap/bootstrap-config.dto'
import { createContainer } from '@/app/bootstrap/container'
import { isOk } from '@/shared/kernel/result'

const fakeLogger: Logger = { debug: jest.fn(), info: jest.fn(), warn: jest.fn(), error: jest.fn() }

const chatBootstrap = () => {
  const result = toChatBootstrap(bootstrapConfigFixture)
  if (!isOk(result)) {
    throw new Error('fixture must parse for this test to be meaningful')
  }
  return result.value
}

describe('createContainer', () => {
  it('builds a container from fakes with no fetch and no DOM', () => {
    const httpClient = new HttpClient()
    const container = createContainer(chatBootstrap(), { httpClient, logger: fakeLogger })

    expect(container.httpClient).toBe(httpClient)
    expect(container.logger).toBe(fakeLogger)
    expect(container.bootstrap.adminPrefix).toBe('/bedrock-chat/admin')
  })

  it('defaults to a real HttpClient and ConsoleLogger when no deps are supplied', () => {
    const container = createContainer(chatBootstrap())

    expect(container.httpClient).toBeInstanceOf(HttpClient)
    expect(container.logger).toBeDefined()
  })
})
