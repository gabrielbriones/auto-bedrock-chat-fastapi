import { describe, expect, it } from '@jest/globals'

import { isErr, isOk } from '@/shared/kernel/result'
import { toFeedbackEntry } from '@/domains/review/infrastructure/dto/feedback-entry.dto'
import {
  encodeReviewQuery,
  fromReviewDecision,
} from '@/domains/review/infrastructure/dto/review-decision.dto'
import { noFilters } from '@/domains/review/domain/public'

import entryFixture from '../../../msw/fixtures/review/feedback-entry.json' with { type: 'json' }

const wireEntry = (overrides: Record<string, unknown> = {}) => ({ ...entryFixture, ...overrides })

describe('toFeedbackEntry', () => {
  it('never throws on a malformed payload — it returns the issues', () => {
    const result = toFeedbackEntry({ id: 'x' })

    expect(isErr(result) && result.error.code).toBe('invalid-response')
    expect(isErr(result) && (result.error.issues?.length ?? 0)).toBeGreaterThan(0)
  })

  it('rejects a timestamp that is not ISO-8601 rather than silently using the epoch', () => {
    const result = toFeedbackEntry(wireEntry({ created_at: 'last tuesday' }))

    expect(isErr(result) && result.error.issues?.join(' ')).toContain('created_at')
  })

  // `conversation_history` is `Dict[str, Any]` server-side, so its keys carry no guarantee.
  // Losing an entire entry because one frame lacks a role would be the worse failure.
  it('normalises a history frame with missing or non-string keys', () => {
    const result = toFeedbackEntry(
      wireEntry({ conversation_history: [{ content: 42 }, { role: 'user', content: 'hi' }] }),
    )

    expect(isOk(result) && result.value.conversationHistory).toEqual([
      { role: 'assistant', content: '' },
      { role: 'user', content: 'hi' },
    ])
  })

  it('reads a KB source with absent fields as nulls', () => {
    const result = toFeedbackEntry(wireEntry({ kb_sources_used: [{}] }))

    expect(isOk(result) && result.value.kbSourcesUsed[0]).toEqual({
      documentId: null,
      title: null,
      source: null,
      url: null,
      score: null,
    })
  })

  it('defaults the collection fields the server may omit entirely', () => {
    const sparse = { ...entryFixture } as Record<string, unknown>
    delete sparse.conversation_history
    delete sparse.kb_sources_used
    delete sparse.reviewer_tags
    delete sparse.entry_metadata

    const result = toFeedbackEntry(sparse)

    expect(isOk(result) && result.value).toMatchObject({
      conversationHistory: [],
      kbSourcesUsed: [],
      reviewerTags: [],
      entryMetadata: {},
    })
  })

  it('has no reviewer decision while the entry is still awaiting review', () => {
    const result = toFeedbackEntry(
      wireEntry({ review_status: 'pending_review', reviewed_at: null, reviewer_id: null }),
    )

    expect(isOk(result) && result.value.review).toBeNull()
  })
})

describe('fromReviewDecision', () => {
  // FIX-05 stated as a body assertion: both keys are always present.
  it('always sends both reviewer fields, so a clear is an instruction and not an omission', () => {
    expect(fromReviewDecision({ decision: 'rejected', tags: [], comment: null })).toEqual({
      review_status: 'rejected',
      reviewer_tags: [],
      reviewer_comment: null,
    })
  })

  it('copies the tag list rather than aliasing the draft', () => {
    const tags = ['emon']
    const body = fromReviewDecision({ decision: 'approved', tags, comment: 'ok' })

    expect(body.reviewer_tags).not.toBe(tags)
    expect(body.reviewer_tags).toEqual(['emon'])
  })
})

describe('encodeReviewQuery', () => {
  it('percent-encodes a tag list rather than sending raw commas', () => {
    const encoded = encodeReviewQuery({ ...noFilters, tags: ['a b', 'c&d'], limit: 25, offset: 0 })

    expect(encoded).toBe('tags=a%20b%2Cc%26d&limit=25&offset=0')
  })

  it('omits an empty tag list', () => {
    expect(encodeReviewQuery({ ...noFilters, limit: 25, offset: 25 })).toBe('limit=25&offset=25')
  })
})
