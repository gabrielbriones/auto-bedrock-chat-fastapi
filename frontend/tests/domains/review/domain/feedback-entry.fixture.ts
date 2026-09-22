import { feedbackEntryId, userId } from '@/shared/kernel/branded'
import { Instant } from '@/shared/kernel/instant'
import { isOk } from '@/shared/kernel/result'

import { NEVER_SYNTHESIZED, type FeedbackEntry, type FeedbackEntrySummary, type ReviewStatus } from '@/domains/review/domain/public'

const at = (iso: string): Instant => {
  const parsed = Instant.fromIso(iso)

  if (!isOk(parsed)) {
    throw new Error(`fixture timestamp is not an instant: ${iso}`)
  }

  return parsed.value
}

export const anInstant = at

export const aSummary = (
  id: string,
  reviewStatus: ReviewStatus = 'pending_review',
  overrides: Partial<FeedbackEntrySummary> = {},
): FeedbackEntrySummary => ({
  id: feedbackEntryId(id),
  userId: userId('rzhang'),
  rating: 'negative',
  reviewStatus,
  query: 'Why is the vectorization ratio so low?',
  createdAt: at('2026-09-02T08:14:03Z'),
  reviewerTags: [],
  ...overrides,
})

export const anEntry = (overrides: Partial<FeedbackEntry> = {}): FeedbackEntry => ({
  ...aSummary('0f0a4a4e-3f4c-4f2c-9a1a-1c6b5f0d1001'),
  sessionId: 'sess-7f21',
  modelId: 'us.anthropic.claude-sonnet-4-20250514-v1:0',
  aiResponse: 'The insprofile shows 12% packed SIMD instructions.',
  correctionText: null,
  userComment: null,
  kbSourcesUsed: [],
  entryMetadata: {},
  conversationHistory: [],
  review: null,
  synthesis: NEVER_SYNTHESIZED,
  ...overrides,
})
