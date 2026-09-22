import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { HttpResponse, http } from 'msw'

import { feedbackEntryId, kbDocumentId } from '@/shared/kernel/branded'
import { CalendarDate } from '@/shared/kernel/instant'
import { HttpClient } from '@/shared/http/http-client'
import { isErr, isOk } from '@/shared/kernel/result'
import { noFilters } from '@/domains/review/domain/public'
import { HttpReviewGateway } from '@/domains/review/infrastructure/http-review.gateway'
import type { ReviewQuery } from '@/domains/review/application/ports'

import { server } from '../../../msw/server'
import { feedbackDeleteConflictHandler, synthesisRunningHandler } from '../../../msw/handlers/review'

const ADMIN = 'http://localhost/bedrock-chat/admin'

const silentLogger = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} }

const gateway = new HttpReviewGateway(ADMIN, new HttpClient(), silentLogger)

const query = (overrides: Partial<ReviewQuery> = {}): ReviewQuery => ({
  ...noFilters,
  limit: 50,
  offset: 0,
  ...overrides,
})

const calendarDate = (iso: string): CalendarDate => {
  const parsed = CalendarDate.fromIso(iso)

  if (!isOk(parsed)) {
    throw new Error(`not a calendar date: ${iso}`)
  }

  return parsed.value
}

const requestUrls: string[] = []

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

describe('HttpReviewGateway.list', () => {
  it('maps the live list envelope down to summaries', async () => {
    const result = await gateway.list(query(), AbortSignal.timeout(1_000))

    expect(isOk(result) && result.value.total).toBe(3)
    expect(isOk(result) && result.value.items[0]).toMatchObject({
      id: '0f0a4a4e-3f4c-4f2c-9a1a-1c6b5f0d1001',
      userId: 'rzhang',
      rating: 'negative',
      reviewStatus: 'pending_review',
    })
  })

  it('omits absent filters and always sends the page', async () => {
    await gateway.list(query(), AbortSignal.timeout(1_000))

    expect(lastUrl()).toBe(`${ADMIN}/feedback?limit=50&offset=0`)
  })

  it('encodes every filter the server understands', async () => {
    await gateway.list(
      query({
        status: 'pending_review',
        rating: 'negative',
        tags: ['emon', 'ipc'],
        dateFrom: calendarDate('2026-09-01'),
        dateTo: calendarDate('2026-09-08'),
        offset: 50,
      }),
      AbortSignal.timeout(1_000),
    )

    expect(lastUrl()).toBe(
      `${ADMIN}/feedback?status=pending_review&rating=negative&tags=emon%2Cipc&date_from=2026-09-01&date_to=2026-09-08&limit=50&offset=50`,
    )
  })

  // FIX-08: the caller can only sequence responses if the request is genuinely abortable.
  it('reports an abort as its own Problem code rather than a network failure', async () => {
    const controller = new AbortController()
    const pending = gateway.list(query(), controller.signal)
    controller.abort()

    const result = await pending

    expect(isErr(result) && result.error.code).toBe('aborted')
  })

  // CT-2: a 200 whose body does not match the schema is a contract failure, not a server error.
  it('reports a malformed payload as invalid-response with the offending path', async () => {
    server.use(http.get(`${ADMIN}/feedback`, () => HttpResponse.json({ items: [], total: 'lots' })))

    const result = await gateway.list(query(), AbortSignal.timeout(1_000))

    expect(isErr(result) && result.error.code).toBe('invalid-response')
    expect(isErr(result) && result.error.issues?.join(' ')).toContain('total')
  })
})

describe('HttpReviewGateway.get', () => {
  it('maps a full entry, including its synthesis state', async () => {
    const result = await gateway.get(feedbackEntryId('any'), AbortSignal.timeout(1_000))

    expect(isOk(result) && result.value.synthesis).toMatchObject({
      kind: 'rolledBack',
      by: 'mokoye',
      reason: 'Superseded by the updated branching runbook.',
    })
    expect(isOk(result) && result.value.review).toMatchObject({
      reviewerId: 'aholt',
      tags: ['branch-prediction'],
    })
  })

  it('percent-encodes the id into the path', async () => {
    await gateway.get(feedbackEntryId('a/b'), AbortSignal.timeout(1_000))

    expect(lastUrl()).toBe(`${ADMIN}/feedback/a%2Fb`)
  })
})

describe('HttpReviewGateway.saveDecision', () => {
  // FIX-05: the whole point of the mapper — a clear must reach the server as an explicit empty.
  it('sends cleared tags and comment explicitly rather than omitting them', async () => {
    let body: unknown

    server.use(
      http.patch(`${ADMIN}/feedback/:id`, async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({ error: 'unused' }, { status: 500 })
      }),
    )

    await gateway.saveDecision(feedbackEntryId('id-1'), { decision: 'approved', tags: [], comment: null })

    expect(body).toEqual({ review_status: 'approved', reviewer_tags: [], reviewer_comment: null })
  })
})

describe('HttpReviewGateway.remove', () => {
  it('succeeds on the server 204, which carries no body to map', async () => {
    expect(isOk(await gateway.remove(feedbackEntryId('id-1')))).toBe(true)
  })

  // I1: the UI must be able to report the refusal, not swallow it.
  it('surfaces the 409 the server returns for a non-rejected entry', async () => {
    server.use(feedbackDeleteConflictHandler)

    const result = await gateway.remove(feedbackEntryId('id-1'))

    expect(isErr(result) && result.error).toMatchObject({ code: 'http-error', status: 409 })
  })
})

describe('HttpReviewGateway reads', () => {
  it('flattens the stats buckets, defaulting the ones the server omits', async () => {
    const result = await gateway.stats(AbortSignal.timeout(1_000))

    expect(isOk(result) && result.value).toMatchObject({
      total: 128,
      pendingReview: 41,
      approved: 66,
      rejected: 21,
      positive: 88,
      negative: 40,
      oldestPendingHours: 73.5,
    })
  })

  it('treats a bucket the server omits as zero', async () => {
    server.use(http.get(`${ADMIN}/feedback/stats`, () => HttpResponse.json({ total: 0 })))

    const result = await gateway.stats(AbortSignal.timeout(1_000))

    expect(isOk(result) && result.value).toMatchObject({ pendingReview: 0, oldestPendingHours: null })
  })

  // FR-REV-020: the badge asks for one row and reads the total off the envelope.
  it('probes the pending count with a single-row page', async () => {
    const result = await gateway.pendingCount(AbortSignal.timeout(1_000))

    expect(lastUrl()).toBe(`${ADMIN}/feedback?status=pending_review&limit=1&offset=0`)
    expect(isOk(result) && result.value).toBe(3)
  })

  it('reads the synthesis phase and nothing else from the run record', async () => {
    expect(isOk(await gateway.synthesisPhase(AbortSignal.timeout(1_000)))).toBe(true)

    server.use(synthesisRunningHandler)
    const running = await gateway.synthesisPhase(AbortSignal.timeout(1_000))

    expect(isOk(running) && running.value).toBe('running')
  })
})

describe('HttpReviewGateway synthesis mutations', () => {
  it('maps a per-entry synthesis result', async () => {
    const result = await gateway.synthesize(feedbackEntryId('id-1'))

    expect(isOk(result) && result.value).toMatchObject({
      tag: 'emon',
      action: 'created',
      kbDocumentId: 'kb-1042',
      markedEntryIds: ['0f0a4a4e-3f4c-4f2c-9a1a-1c6b5f0d1002'],
    })
  })

  it('sends the rollback reason, including an explicit absence of one', async () => {
    let body: unknown

    server.use(
      http.post(`${ADMIN}/synthesis/rollback/:id`, async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({ error: 'unused' }, { status: 500 })
      }),
    )

    await gateway.rollback(kbDocumentId('kb-1042'), null)

    expect(body).toEqual({ reason: null })
  })

  it('maps the rollback outcome, including how many entries were reverted', async () => {
    const result = await gateway.rollback(kbDocumentId('kb-1042'), 'Contradicted.')

    expect(isOk(result) && result.value).toMatchObject({
      kbDocumentId: 'kb-1042',
      rolledBackBy: 'aholt',
      entriesReverted: 3,
    })
  })
})
