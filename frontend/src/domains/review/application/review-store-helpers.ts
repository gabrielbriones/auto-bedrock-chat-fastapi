import type { FeedbackEntryId } from '@/shared/kernel/branded'
import type { ConfirmationPort } from '@/shared/ports/confirmation-port'
import type { Logger } from '@/shared/logging/logger'
import type { NotificationPort } from '@/shared/ports/notification-port'
import { REVIEW_COPY } from '@/shared/copy/review'
import { isErr } from '@/shared/kernel/result'
import { createReviewQueue, shouldStepBack, type ReviewQueue } from '@/domains/review/domain/public'

import type { ReviewGateway } from '@/domains/review/application/ports'
import type { ReviewSnapshot } from '@/domains/review/application/review-snapshot'

export function createInitialReviewSnapshot(): ReviewSnapshot {
  return {
    queue: createReviewQueue({
      status: null,
      rating: null,
      tags: [],
      dateFrom: null,
      dateTo: null,
    }),
    listStatus: 'idle',
    listProblem: null,
    activeEntry: null,
    detailStatus: 'idle',
    detailProblem: null,
    mutationPending: false,
    saveProblem: null,
    failedDeleteIds: [],
    synthesisPhase: null,
    synthesisPending: false,
    synthesisProblem: null,
    pendingCount: null,
    stats: null,
    statsStatus: 'idle',
    statsProblem: null,
  }
}

// FR-REV-015a: a reason is asked for first (optional); an empty answer is confirmed a second
// time before the rollback is sent, so a blank reason is a deliberate choice rather than a slip.
// Returns null when the flow is cancelled at either step, otherwise the (possibly empty) reason.
export async function collectRollbackReason(confirmations: ConfirmationPort): Promise<string | null> {
  const reason = await confirmations.prompt({
    title: REVIEW_COPY.synthesis.rollbackPromptTitle,
    message: REVIEW_COPY.synthesis.rollbackPromptMessage,
    label: REVIEW_COPY.synthesis.rollbackPromptLabel,
    confirmLabel: REVIEW_COPY.synthesis.rollbackPromptConfirm,
    optional: true,
  })

  if (reason === null) {
    return null
  }

  const trimmed = reason.trim()

  if (trimmed.length === 0) {
    const confirmed = await confirmations.confirm({
      title: REVIEW_COPY.synthesis.rollbackConfirmTitle,
      message: REVIEW_COPY.synthesis.rollbackConfirmMessage,
      confirmLabel: REVIEW_COPY.synthesis.rollbackConfirmLabel,
      tone: 'destructive',
    })

    if (!confirmed) {
      return null
    }
  }

  return trimmed
}

export function reportBulkDeleteOutcome(
  notifications: NotificationPort,
  logger: Logger,
  requested: number,
  deleted: number,
  failedIds: readonly FeedbackEntryId[],
): void {
  if (failedIds.length === 0) {
    notifications.success(REVIEW_COPY.bulk.success(deleted))
    return
  }

  logger.warn('review_bulk_delete_partial', { requested, deleted, failed: failedIds.length })
  notifications.error(REVIEW_COPY.bulk.partial(deleted, failedIds.length), {
    description: REVIEW_COPY.bulk.failedIds(failedIds),
    durationMs: 0,
  })
}

// Deletions run concurrently; the ids that failed are named so the caller can keep them visible.
export async function deleteEntries(
  gateway: ReviewGateway,
  ids: readonly FeedbackEntryId[],
): Promise<readonly FeedbackEntryId[]> {
  const results = await Promise.all(ids.map(async (id) => ({ id, result: await gateway.remove(id) })))
  return results.filter(({ result }) => isErr(result)).map(({ id }) => id)
}

export function previousOffsetAfterDelete(queue: ReviewQueue): number | null {
  return shouldStepBack(queue) ? Math.max(0, queue.page.offset - queue.page.limit) : null
}

// Opening and closing the drawer both drop everything scoped to the previously shown entry.
export function withDetailReset(
  snapshot: ReviewSnapshot,
  detailStatus: ReviewSnapshot['detailStatus'],
): ReviewSnapshot {
  return {
    ...snapshot,
    activeEntry: null,
    detailStatus,
    detailProblem: null,
    saveProblem: null,
    synthesisPhase: null,
    synthesisProblem: null,
  }
}
