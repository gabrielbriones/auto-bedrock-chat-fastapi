import type { FeedbackEntryId, KbDocumentId } from '@/shared/kernel/branded'
import type { Instant } from '@/shared/kernel/instant'
import type { Result } from '@/shared/kernel/result'
import type { OffsetPage } from '@/shared/kernel/pagination'
import type { Problem } from '@/shared/http/exception'

import type {
  FeedbackEntry,
  FeedbackEntrySummary,
  ReviewDecisionDraft,
  ReviewFilters,
  SynthesisPhase,
} from '@/domains/review/domain/public'

export type ReviewQuery = ReviewFilters & OffsetPage

export type Page<T> = {
  readonly items: readonly T[]
  readonly total: number
  readonly limit: number
  readonly offset: number
}

export type FeedbackStats = {
  readonly total: number
  readonly pendingReview: number
  readonly approved: number
  readonly rejected: number
  readonly positive: number
  readonly negative: number
  readonly withCorrection: number
  readonly integrated: number
  readonly oldestPendingHours: number | null
  readonly topTags: readonly { readonly tag: string; readonly count: number }[]
}

export type SynthesisOutcome = {
  readonly tag: string
  readonly action: string
  readonly kbDocumentId: KbDocumentId | null
  readonly markedEntryIds: readonly FeedbackEntryId[]
}

export type RollbackOutcome = {
  readonly kbDocumentId: KbDocumentId
  readonly rolledBackAt: Instant
  readonly rolledBackBy: string
  readonly reason: string | null
  readonly entriesReverted: number
}

// SPEC-016 §3. Every read takes a signal because FIX-08 is a sequencing bug the caller can only
// fix if it can abort; the mutations do not, because a half-applied decision is worse than a slow
// one. Nothing here throws — transport and contract failures are both `Problem`.
export interface ReviewGateway {
  list(query: ReviewQuery, signal: AbortSignal): Promise<Result<Page<FeedbackEntrySummary>, Problem>>
  get(id: FeedbackEntryId, signal: AbortSignal): Promise<Result<FeedbackEntry, Problem>>
  saveDecision(id: FeedbackEntryId, draft: ReviewDecisionDraft): Promise<Result<FeedbackEntry, Problem>>
  remove(id: FeedbackEntryId): Promise<Result<void, Problem>>
  stats(signal: AbortSignal): Promise<Result<FeedbackStats, Problem>>
  pendingCount(signal: AbortSignal): Promise<Result<number, Problem>>
  synthesize(id: FeedbackEntryId): Promise<Result<SynthesisOutcome, Problem>>
  rollback(articleId: KbDocumentId, reason: string | null): Promise<Result<RollbackOutcome, Problem>>
  synthesisPhase(signal: AbortSignal): Promise<Result<SynthesisPhase, Problem>>
}
