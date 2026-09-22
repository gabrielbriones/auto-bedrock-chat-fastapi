import type { Problem } from '@/shared/http/exception'
import type { FeedbackEntryId } from '@/shared/kernel/branded'
import type { ConfirmationPort } from '@/shared/ports/confirmation-port'
import type { Logger } from '@/shared/logging/logger'
import type { NotificationPort } from '@/shared/ports/notification-port'

import type { FeedbackStats, ReviewGateway } from '@/domains/review/application/ports'
import type { FeedbackEntry, ReviewQueue, SynthesisPhase } from '@/domains/review/domain/public'

export type ReviewListStatus = 'idle' | 'loading' | 'ready' | 'error'

// FR-REV-015b/015c: the same status code means something different depending on which action
// failed, so the problem carries which one it answers rather than making the UI guess.
export type SynthesisProblem = {
  readonly kind: 'synthesize' | 'rollback'
  readonly problem: Problem
}

export type ReviewSnapshot = {
  readonly queue: ReviewQueue
  readonly listStatus: ReviewListStatus
  readonly listProblem: Problem | null
  readonly activeEntry: FeedbackEntry | null
  readonly detailStatus: ReviewListStatus
  readonly detailProblem: Problem | null
  readonly mutationPending: boolean
  readonly saveProblem: Problem | null
  readonly failedDeleteIds: readonly FeedbackEntryId[]
  readonly synthesisPhase: SynthesisPhase | null
  readonly synthesisPending: boolean
  readonly synthesisProblem: SynthesisProblem | null
  readonly pendingCount: number | null
  readonly stats: FeedbackStats | null
  readonly statsStatus: ReviewListStatus
  readonly statsProblem: Problem | null
}

export type BulkDeleteOutcome = {
  readonly deleted: number
  readonly failedIds: readonly FeedbackEntryId[]
  readonly previousOffset: number | null
}

export type ReviewStoreOptions = {
  readonly gateway: ReviewGateway
  readonly confirmations: ConfirmationPort
  readonly notifications: NotificationPort
  readonly logger: Logger
}
