import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { isErr, isOk } from '@/shared/kernel/result'
import { HttpClient } from '@/shared/http/http-client'

import { server } from '../../msw/server'
import {
  bootstrapHtml502Handler,
  bootstrapMalformedHandler,
  bootstrapNetworkErrorHandler,
} from '../../msw/handlers/bootstrap'
import { chatBase, loadBootstrap } from '@/app/bootstrap/loadBootstrap'

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

describe('loadBootstrap', () => {
  it('resolves ok(ChatBootstrap) for a valid response', async () => {
    const result = await loadBootstrap(new HttpClient(), 'http://localhost/bedrock-chat')

    expect(isOk(result)).toBe(true)
    if (isOk(result)) {
      expect(result.value.adminPrefix).toBe('/bedrock-chat/admin')
    }
  })

  it('returns err for a malformed/incomplete response', async () => {
    server.use(bootstrapMalformedHandler)

    const result = await loadBootstrap(new HttpClient(), 'http://localhost/bedrock-chat')

    expect(isErr(result)).toBe(true)
    if (isErr(result)) {
      expect(result.error.code).toBe('invalid-response')
    }
  })

  it('surfaces a readable title for a 502 with an HTML body (FIX-07)', async () => {
    server.use(bootstrapHtml502Handler)

    const result = await loadBootstrap(new HttpClient(), 'http://localhost/bedrock-chat')

    expect(isErr(result)).toBe(true)
    if (isErr(result)) {
      expect(result.error).toEqual({ code: 'http-error', status: 502, title: 'Bad Gateway' })
    }
  })

  it('returns a network-error Problem when the request fails outright', async () => {
    server.use(bootstrapNetworkErrorHandler)

    const result = await loadBootstrap(new HttpClient(), 'http://localhost/bedrock-chat')

    expect(isErr(result)).toBe(true)
    if (isErr(result)) {
      expect(result.error.code).toBe('network-error')
    }
  })

  it('uses the provided base URL instead of a hardcoded prefix', async () => {
    const result = await loadBootstrap(new HttpClient(), 'http://localhost/custom-base')

    // No handler is registered for /custom-base/config, so this must fail rather than
    // silently hit the default /bedrock-chat/config handler.
    expect(isErr(result)).toBe(true)
  })
})

describe('chatBase', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('defaults to /bedrock-chat when VITE_CHAT_BASE is unset', () => {
    vi.stubEnv('VITE_CHAT_BASE', '')
    expect(chatBase()).toBe('/bedrock-chat')
  })

  it('uses VITE_CHAT_BASE when it is set', () => {
    vi.stubEnv('VITE_CHAT_BASE', '/custom-chat')
    expect(chatBase()).toBe('/custom-chat')
  })
})
