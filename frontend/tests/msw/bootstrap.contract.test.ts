import { afterAll, afterEach, beforeAll, describe, expect, it } from '@jest/globals'

import { server } from './server'

// Contract-project smoke test (Task 07 exit criterion: one smoke test per runner).
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

describe('bootstrap config contract', () => {
  it('GET {CHAT}/config matches the CONTRACT-001 §6 shape', async () => {
    const response = await fetch('http://localhost/bedrock-chat/config')
    const body = await response.json()

    expect(response.status).toBe(200)
    expect(body).toMatchObject({
      websocketUrl: expect.any(String),
      authEnabled: expect.any(Boolean),
      supportedAuthTypes: expect.any(Array),
      adminPrefix: expect.any(String),
    })
  })
})
