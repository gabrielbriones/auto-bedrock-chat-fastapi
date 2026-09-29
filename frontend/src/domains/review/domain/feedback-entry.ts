import type { FeedbackEntryId, UserId } from '@/shared/kernel/branded'
import type { Instant } from '@/shared/kernel/instant'
import { err, isErr, ok, type Result } from '@/shared/kernel/result'
import { validateTags, type TagPolicyError } from '@/shared/kernel/curation'

import type { SynthesisState } from '@/domains/review/domain/synthesis-state'

export type Rating = 'positive' | 'negative'

export type ReviewStatus = 'pending_review' | 'approved' | 'rejected'

/** I3: a decision is never blank — `pending_review` is not a reviewable outcome. */
export type ReviewDecision = 'approved' | 'rejected'

export type ReviewMessage = {
  readonly role: string
  readonly content: string
}

export type KbSource = {
  readonly documentId: string | null
  readonly title: string | null
  readonly source: string | null
  readonly url: string | null
  readonly score: number | null
}

export type ReviewerDecision = {
  readonly reviewerId: string | null
  readonly reviewedAt: Instant | null
  readonly tags: readonly string[]
  readonly comment: string | null
}

/** The queue and reviewed tables read only these columns (FR-REV-004). */
export type FeedbackEntrySummary = {
  readonly id: FeedbackEntryId
  readonly userId: UserId
  readonly rating: Rating
  readonly reviewStatus: ReviewStatus
  readonly query: string
  readonly createdAt: Instant
  readonly reviewerTags: readonly string[]
}

// DESIGN-001 §6.1 aggregate root. Loaded whole by the drawer; the tables hold summaries.
export type FeedbackEntry = FeedbackEntrySummary & {
  readonly sessionId: string
  readonly modelId: string
  readonly aiResponse: string
  readonly correctionText: string | null
  readonly userComment: string | null
  readonly kbSourcesUsed: readonly KbSource[]
  readonly entryMetadata: Readonly<Record<string, unknown>>
  readonly conversationHistory: readonly ReviewMessage[]
  readonly review: ReviewerDecision | null
  readonly synthesis: SynthesisState
}

export const toSummary = (entry: FeedbackEntry): FeedbackEntrySummary => ({
  id: entry.id,
  userId: entry.userId,
  rating: entry.rating,
  reviewStatus: entry.reviewStatus,
  query: entry.query,
  createdAt: entry.createdAt,
  reviewerTags: entry.reviewerTags,
})

// I1. The server answers 409 for anything else, so the UI must not offer the action at all —
// selection on the Reviewed page is built on this predicate rather than a status string compare.
export const isDeletable = (entry: Pick<FeedbackEntrySummary, 'reviewStatus'>): boolean =>
  entry.reviewStatus === 'rejected'

/** FR-REV-015: the synthesis section exists only for entries a reviewer approved. */
export const isSynthesisEligible = (entry: Pick<FeedbackEntrySummary, 'reviewStatus'>): boolean =>
  entry.reviewStatus === 'approved'

export type ReviewDecisionDraft = {
  readonly decision: ReviewDecision
  readonly tags: readonly string[]
  /** `null` is an explicit clear, not "leave unchanged" (FIX-05). */
  readonly comment: string | null
}

export type ReviewDraftError = { readonly kind: 'decision-required' } | TagPolicyError

// I2 + I3 in one gate: the form cannot produce a draft that the server would reject, and a
// cleared comment survives as `null` rather than being dropped on the way through.
export const createReviewDecisionDraft = (input: {
  readonly decision: ReviewDecision | null
  readonly tags: readonly string[]
  readonly comment: string | null
}): Result<ReviewDecisionDraft, ReviewDraftError> => {
  if (input.decision === null) {
    return err({ kind: 'decision-required' })
  }

  const tags = validateTags(input.tags)
  if (isErr(tags)) {
    return tags
  }

  const comment = input.comment === null ? null : input.comment.trim()

  return ok({
    decision: input.decision,
    tags: tags.value,
    comment: comment === null || comment.length === 0 ? null : comment,
  })
}

// FR-REV-014: only a decision matching the entry's current status is pre-selected, so an entry
// still in the queue opens with nothing chosen and cannot be saved by a stray Enter.
export const initialDecision = (
  entry: Pick<FeedbackEntrySummary, 'reviewStatus'>,
): ReviewDecision | null => (entry.reviewStatus === 'pending_review' ? null : entry.reviewStatus)
