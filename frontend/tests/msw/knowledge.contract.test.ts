import { afterAll, afterEach, beforeAll, describe, expect, it } from '@jest/globals'

import { isOk } from '@/shared/kernel/result'
import { toKbDocument, toKbDocumentPage } from '@/domains/knowledge/infrastructure/dto/kb-document.dto'
import {
  toKbSourceDeletion,
  toKbSourceRun,
  toKbSourceSummaries,
} from '@/domains/knowledge/infrastructure/dto/kb-source.dto'

import { server } from './server'

// CT-2, for the knowledge context: every fixture the handlers answer with is parsed by the same
// production mapper the app uses, so a fixture drifting from admin-api.md fails here first.
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const ADMIN = 'http://localhost/chat/admin'

const fetchJson = async (path: string, init?: RequestInit, status = 200): Promise<unknown> => {
  const response = await fetch(`${ADMIN}${path}`, init)

  expect(response.status).toBe(status)
  return response.json()
}

describe('knowledge REST contract', () => {
  it('GET /kb/documents parses into a page of summaries', async () => {
    expect(isOk(toKbDocumentPage(await fetchJson('/kb/documents?limit=50&offset=0')))).toBe(true)
  })

  it('GET /kb/documents/{id} parses into a document', async () => {
    expect(isOk(toKbDocument(await fetchJson('/kb/documents/any')))).toBe(true)
  })

  it('GET /kb/sources parses into source summaries', async () => {
    expect(isOk(toKbSourceSummaries(await fetchJson('/kb/sources')))).toBe(true)
  })

  it('GET /kb/sources/status parses into a run', async () => {
    expect(isOk(toKbSourceRun(await fetchJson('/kb/sources/status')))).toBe(true)
  })

  it.each([
    ['POST', '/kb/sources/web'],
    ['PUT', '/kb/sources/web/intel-docs'],
    ['POST', '/kb/sources/file'],
    ['PUT', '/kb/sources/file/intel-docs'],
  ])('%s %s answers 202 with the run status', async (method, path) => {
    const body = await fetchJson(path, { method, body: new FormData() }, 202)

    expect(isOk(toKbSourceRun(body))).toBe(true)
  })

  it('DELETE /kb/sources/{name} parses the 200 response into a deletion result', async () => {
    expect(isOk(toKbSourceDeletion(await fetchJson('/kb/sources/feedback', { method: 'DELETE' })))).toBe(true)
  })
})
