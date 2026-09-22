import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'

import { isOk } from '@/shared/kernel/result'
import { toFeedbackEntry, toFeedbackEntryPage } from '@/domains/review/infrastructure/dto/feedback-entry.dto'
import { toFeedbackStats } from '@/domains/review/infrastructure/dto/feedback-stats.dto'
import {
  toRollbackOutcome,
  toSynthesisOutcome,
  toSynthesisPhase,
} from '@/domains/review/infrastructure/dto/synthesis.dto'

import { server } from './server'

// CT-2. The fixtures are shaped from the live backend's own OpenAPI document, and each one is
// parsed here by the same production mapper the app uses — so a fixture that drifts from the
// schema, or a schema that drifts from the server, fails the build rather than a runtime view.
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const ADMIN = 'http://localhost/bedrock-chat/admin'

const fetchJson = async (path: string, init?: RequestInit): Promise<unknown> => {
  const response = await fetch(`${ADMIN}${path}`, init)

  expect(response.status).toBe(200)
  return response.json()
}

describe('review REST contract (SPEC-016 §3)', () => {
  it('GET /feedback parses into a page of summaries', async () => {
    expect(isOk(toFeedbackEntryPage(await fetchJson('/feedback?limit=50&offset=0')))).toBe(true)
  })

  it('GET /feedback/{id} parses into an entry', async () => {
    expect(isOk(toFeedbackEntry(await fetchJson('/feedback/any')))).toBe(true)
  })

  it('PATCH /feedback/{id} answers with the updated entry', async () => {
    const body = await fetchJson('/feedback/any', {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ review_status: 'approved', reviewer_tags: [], reviewer_comment: null }),
    })

    expect(isOk(toFeedbackEntry(body))).toBe(true)
  })

  it('DELETE /feedback/{id} answers 204 with no body', async () => {
    const response = await fetch(`${ADMIN}/feedback/any`, { method: 'DELETE' })

    expect(response.status).toBe(204)
  })

  it('GET /feedback/stats parses into the stats projection', async () => {
    expect(isOk(toFeedbackStats(await fetchJson('/feedback/stats')))).toBe(true)
  })

  it('GET /synthesis/status parses into a phase (BC-007)', async () => {
    expect(isOk(toSynthesisPhase(await fetchJson('/synthesis/status')))).toBe(true)
  })

  it('POST /synthesis/trigger/{id} parses into a synthesis outcome', async () => {
    expect(isOk(toSynthesisOutcome(await fetchJson('/synthesis/trigger/any', { method: 'POST' })))).toBe(true)
  })

  it('POST /synthesis/rollback/{id} parses into a rollback outcome', async () => {
    const body = await fetchJson('/synthesis/rollback/kb-1042', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ reason: null }),
    })

    expect(isOk(toRollbackOutcome(body))).toBe(true)
  })
})
