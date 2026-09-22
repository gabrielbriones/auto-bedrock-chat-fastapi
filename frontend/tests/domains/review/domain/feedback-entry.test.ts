import { describe, expect, it } from 'vitest'

import { isErr, isOk } from '@/shared/kernel/result'
import {
  createReviewDecisionDraft,
  initialDecision,
  isDeletable,
  isSynthesisEligible,
  toSummary,
} from '@/domains/review/domain/public'

import { aSummary, anEntry } from './feedback-entry.fixture'

describe('FeedbackEntry invariants', () => {
  // I1 (DESIGN-001 §6.1): the server answers 409 for anything else, so the UI must never offer it.
  it('permits deletion only for a rejected entry', () => {
    expect(isDeletable(aSummary('a', 'rejected'))).toBe(true)
    expect(isDeletable(aSummary('b', 'approved'))).toBe(false)
    expect(isDeletable(aSummary('c', 'pending_review'))).toBe(false)
  })

  it('offers synthesis only for an approved entry', () => {
    expect(isSynthesisEligible(aSummary('a', 'approved'))).toBe(true)
    expect(isSynthesisEligible(aSummary('b', 'rejected'))).toBe(false)
  })

  it('projects the aggregate down to the columns a table reads', () => {
    const entry = anEntry({ reviewerTags: ['emon'] })

    expect(toSummary(entry)).toEqual({
      id: entry.id,
      userId: entry.userId,
      rating: entry.rating,
      reviewStatus: entry.reviewStatus,
      query: entry.query,
      createdAt: entry.createdAt,
      reviewerTags: ['emon'],
    })
  })
})

describe('initialDecision', () => {
  // FR-REV-014: a queued entry opens with nothing selected, so a stray Enter cannot decide it.
  it('pre-selects nothing for an entry still awaiting review', () => {
    expect(initialDecision(aSummary('a', 'pending_review'))).toBeNull()
  })

  it('pre-selects the decision already recorded', () => {
    expect(initialDecision(aSummary('a', 'approved'))).toBe('approved')
    expect(initialDecision(aSummary('b', 'rejected'))).toBe('rejected')
  })
})

describe('createReviewDecisionDraft', () => {
  it('requires a decision (I3)', () => {
    const result = createReviewDecisionDraft({ decision: null, tags: [], comment: 'looks right' })

    expect(isErr(result) && result.error).toEqual({ kind: 'decision-required' })
  })

  it('enforces the tag policy (I2)', () => {
    const result = createReviewDecisionDraft({ decision: 'approved', tags: ['not a tag'], comment: null })

    expect(isErr(result) && result.error).toMatchObject({ kind: 'invalid-tag' })
  })

  // FIX-05: a cleared field has to survive as an explicit empty value all the way to the body.
  it('keeps a cleared comment as an explicit null rather than dropping it', () => {
    const result = createReviewDecisionDraft({ decision: 'rejected', tags: [], comment: null })

    expect(isOk(result) && result.value).toEqual({ decision: 'rejected', tags: [], comment: null })
  })

  it('treats a whitespace-only comment as cleared', () => {
    const result = createReviewDecisionDraft({ decision: 'approved', tags: [], comment: '   ' })

    expect(isOk(result) && result.value.comment).toBeNull()
  })

  it('trims a comment that has content', () => {
    const result = createReviewDecisionDraft({ decision: 'approved', tags: ['emon'], comment: '  good  ' })

    expect(isOk(result) && result.value).toEqual({ decision: 'approved', tags: ['emon'], comment: 'good' })
  })
})
