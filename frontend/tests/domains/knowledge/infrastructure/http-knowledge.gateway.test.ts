import { afterAll, afterEach, beforeAll, describe, expect, it } from '@jest/globals'
import { HttpResponse, http } from 'msw'

import { kbDocumentId } from '@/shared/kernel/branded'
import { HttpClient } from '@/shared/http/http-client'
import { CalendarDate } from '@/shared/kernel/instant'
import { isErr, isOk } from '@/shared/kernel/result'
import type { KbQuery } from '@/domains/knowledge/application/ports'
import { HttpKnowledgeGateway } from '@/domains/knowledge/infrastructure/http-knowledge.gateway'

import kbDocument from '../../../msw/fixtures/knowledge/kb-document.json' with { type: 'json' }
import { server } from '../../../msw/server'

const ADMIN = 'http://localhost/bedrock-chat/admin'
const silentLogger = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} }
const gateway = new HttpKnowledgeGateway(ADMIN, new HttpClient(), silentLogger)
const requestUrls: string[] = []

const calendarDate = (value: string): CalendarDate => {
  const result = CalendarDate.fromIso(value)

  if (!isOk(result)) {
    throw new Error(`invalid test date: ${value}`)
  }

  return result.value
}

const query = (overrides: Partial<KbQuery> = {}): KbQuery => ({
  source: null,
  topic: null,
  tags: [],
  dateFrom: null,
  dateTo: null,
  removalFlagged: false,
  limit: 50,
  offset: 0,
  ...overrides,
})

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' })
  server.events.on('request:start', ({ request }) => {
    requestUrls.push(request.url)
  })
})
afterEach(() => {
  server.resetHandlers()
  requestUrls.length = 0
})
afterAll(() => server.close())

const lastUrl = (): string => requestUrls[requestUrls.length - 1] ?? ''

describe('HttpKnowledgeGateway.list', () => {
  it('maps list documents into summaries and preserves credibility', async () => {
    const result = await gateway.list(query(), AbortSignal.timeout(1_000))

    expect(isOk(result) && result.value).toMatchObject({ total: 1, limit: 50, offset: 0 })
    expect(isOk(result) && result.value.items[0]).toMatchObject({
      id: 'kb/2026/perf-guide',
      title: 'Performance guide',
      credibility: { score: 0.8, removalFlagged: false, band: 'good' },
    })
  })

  it('encodes every list filter, omits empty ones, and sends flagged only without an unflagged mode', async () => {
    await gateway.list(query(), AbortSignal.timeout(1_000))

    expect(lastUrl()).toBe(`${ADMIN}/kb/documents?limit=50&offset=0`)

    await gateway.list(
      query({
        source: 'ISS docs',
        topic: 'memory/cache',
        tags: ['ipc', 'perf guide'],
        dateFrom: calendarDate('2026-09-01'),
        dateTo: calendarDate('2026-09-08'),
        removalFlagged: true,
        offset: 50,
      }),
      AbortSignal.timeout(1_000),
    )

    expect(lastUrl()).toBe(
      `${ADMIN}/kb/documents?source=ISS%20docs&topic=memory%2Fcache&tags=ipc%2Cperf%20guide&date_from=2026-09-01&date_to=2026-09-08&removal_flagged=true&limit=50&offset=50`,
    )
  })

  it('reports an aborted read with the dedicated Problem code', async () => {
    const controller = new AbortController()
    const pending = gateway.list(query(), controller.signal)
    controller.abort()

    const result = await pending

    expect(isErr(result) && result.error.code).toBe('aborted')
  })

  it('reports a malformed list as invalid-response', async () => {
    server.use(http.get(`${ADMIN}/kb/documents`, () => HttpResponse.json({ items: [], total: 'many' })))

    const result = await gateway.list(query(), AbortSignal.timeout(1_000))

    expect(isErr(result) && result.error.code).toBe('invalid-response')
    expect(isErr(result) && result.error.issues?.join(' ')).toContain('total')
  })
})

describe('HttpKnowledgeGateway document paths', () => {
  it('maps a detail response, including nullable content and defaulted fields', async () => {
    const result = await gateway.get(kbDocumentId('kb/2026/perf-guide'), AbortSignal.timeout(1_000))

    expect(isOk(result) && result.value).toMatchObject({
      id: 'kb/2026/perf-guide',
      content: null,
      chunkCount: null,
      credibility: { score: 0.8, removalFlagged: false, band: 'good' },
    })
  })

  it('percent-encodes a slash-bearing id for detail, patch, reset, and delete requests', async () => {
    await gateway.get(kbDocumentId('https://example.com/kb/perf-guide'), AbortSignal.timeout(1_000))

    expect(lastUrl()).toBe(`${ADMIN}/kb/documents/https%3A%2F%2Fexample.com%2Fkb%2Fperf-guide`)

    const id = kbDocumentId('kb/2026/perf-guide')
    let patchBody: unknown

    server.use(
      http.patch(`${ADMIN}/kb/documents/:id`, async ({ request }) => {
        patchBody = await request.json()
        return HttpResponse.json(kbDocument)
      }),
    )

    await gateway.patch(id, {
      title: null,
      topic: 'memory',
      tags: [],
      content: '',
      datePublished: null,
      metadata: {},
    })

    expect(patchBody).toEqual({
      title: null,
      topic: 'memory',
      tags: [],
      content: '',
      date_published: null,
      metadata: {},
    })
    expect(lastUrl()).toBe(`${ADMIN}/kb/documents/kb%2F2026%2Fperf-guide`)

    await gateway.resetCredibility(id)
    expect(lastUrl()).toBe(`${ADMIN}/kb/documents/reset-credibility/kb%2F2026%2Fperf-guide`)

    expect(isOk(await gateway.remove(id))).toBe(true)
    expect(lastUrl()).toBe(`${ADMIN}/kb/documents/kb%2F2026%2Fperf-guide`)
  })
})

describe('HttpKnowledgeGateway rollback', () => {
  it('sends an explicit null reason and maps the rollback result', async () => {
    let rollbackBody: unknown

    server.use(
      http.post(`${ADMIN}/synthesis/rollback/:id`, async ({ request }) => {
        rollbackBody = await request.json()
        return HttpResponse.json({
          article_id: 'kb/2026/perf-guide',
          rolled_back_at: '2026-09-08T10:15:44Z',
          rolled_back_by: 'aholt',
          reason: null,
          feedback_entries_reverted: 3,
        })
      }),
    )

    const result = await gateway.rollback(kbDocumentId('kb/2026/perf-guide'), null)

    expect(rollbackBody).toEqual({ reason: null })
    expect(isOk(result) && result.value.kbDocumentId).toBe('kb/2026/perf-guide')
    expect(lastUrl()).toBe(`${ADMIN}/synthesis/rollback/kb%2F2026%2Fperf-guide`)
  })
})