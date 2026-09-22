import type { FeedbackEntryId, KbDocumentId } from '@/shared/kernel/branded'
import { REVIEW_COPY } from '@/shared/copy/review'
import { isErr } from '@/shared/kernel/result'

import type { ReviewQuery } from '@/domains/review/application/ports'
import {
  clearSelection,
  createReviewQueue,
  selectAllSelectable,
  selectionState,
  toggleSelection,
  type ReviewDecisionDraft,
  withPage,
} from '@/domains/review/domain/public'
import type {
  BulkDeleteOutcome,
  ReviewSnapshot,
  ReviewStoreOptions,
} from '@/domains/review/application/review-snapshot'
import {
  collectRollbackReason,
  createInitialReviewSnapshot,
  deleteEntries,
  previousOffsetAfterDelete,
  reportBulkDeleteOutcome,
  withDetailReset,
} from '@/domains/review/application/review-store-helpers'

export type {
  BulkDeleteOutcome,
  ReviewListStatus,
  ReviewSnapshot,
  ReviewStoreOptions,
  SynthesisProblem,
} from '@/domains/review/application/review-snapshot'

export class ReviewStore {
  readonly #options: ReviewStoreOptions
  readonly #listeners = new Set<() => void>()
  #listController: AbortController | null = null
  #listSequence = 0
  #detailController: AbortController | null = null
  #detailSequence = 0
  #statsSequence = 0
  #pendingCountController: AbortController | null = null
  #pendingCountSequence = 0
  #currentQuery: ReviewQuery | null = null
  #snapshot: ReviewSnapshot

  constructor(options: ReviewStoreOptions) {
    this.#options = options
    this.#snapshot = createInitialReviewSnapshot()
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener)
    return () => {
      this.#listeners.delete(listener)
    }
  }

  getSnapshot = (): ReviewSnapshot => this.#snapshot

  async load(query: ReviewQuery): Promise<void> {
    this.#currentQuery = query
    this.#listController?.abort()
    const controller = new AbortController()
    const sequence = ++this.#listSequence
    this.#listController = controller
    this.#snapshot = {
      ...this.#snapshot,
      queue: createReviewQueue(query, { limit: query.limit, offset: query.offset }),
      listStatus: 'loading',
      listProblem: null,
    }
    this.#emit()

    const result = await this.#options.gateway.list(query, controller.signal)

    if (sequence !== this.#listSequence || controller.signal.aborted) {
      return
    }

    if (isErr(result)) {
      this.#snapshot = { ...this.#snapshot, listStatus: 'error', listProblem: result.error }
    } else {
      this.#snapshot = {
        ...this.#snapshot,
        queue: withPage(this.#snapshot.queue, {
          entries: result.value.items,
          total: result.value.total,
          offset: result.value.offset,
        }),
        listStatus: 'ready',
        listProblem: null,
      }
    }

    this.#emit()
  }

  async open(id: FeedbackEntryId): Promise<void> {
    this.#detailController?.abort()
    const controller = new AbortController()
    const sequence = ++this.#detailSequence
    this.#detailController = controller
    this.#snapshot = withDetailReset(this.#snapshot, 'loading')
    this.#emit()

    // FR-REV-016: the per-entry synthesize action is gated on the batch phase, so it is fetched
    // alongside the entry rather than only when someone reaches for the button.
    const [result, phaseResult] = await Promise.all([
      this.#options.gateway.get(id, controller.signal),
      this.#options.gateway.synthesisPhase(controller.signal),
    ])

    if (sequence !== this.#detailSequence || controller.signal.aborted) {
      return
    }

    this.#snapshot = isErr(result)
      ? { ...this.#snapshot, detailStatus: 'error', detailProblem: result.error }
      : {
          ...this.#snapshot,
          activeEntry: result.value,
          detailStatus: 'ready',
          detailProblem: null,
          synthesisPhase: isErr(phaseResult) ? null : phaseResult.value,
        }
    this.#emit()
  }

  close(): void {
    this.#detailController?.abort()
    this.#detailSequence += 1
    this.#snapshot = withDetailReset(this.#snapshot, 'idle')
    this.#emit()
  }

  toggleSelected(id: FeedbackEntryId): void {
    this.#snapshot = { ...this.#snapshot, queue: toggleSelection(this.#snapshot.queue, id) }
    this.#emit()
  }

  toggleSelectAll(): void {
    const queue =
      selectionState(this.#snapshot.queue) === 'all'
        ? clearSelection(this.#snapshot.queue)
        : selectAllSelectable(this.#snapshot.queue)
    this.#snapshot = { ...this.#snapshot, queue }
    this.#emit()
  }

  clearSelected(): void {
    this.#snapshot = { ...this.#snapshot, queue: clearSelection(this.#snapshot.queue) }
    this.#emit()
  }

  async removeSelected(): Promise<BulkDeleteOutcome | null> {
    const ids = [...this.#snapshot.queue.selection]

    if (ids.length === 0 || this.#isBusy()) {
      return null
    }

    const confirmed = await this.#options.confirmations.confirm({
      title: REVIEW_COPY.bulk.title(ids.length),
      message: REVIEW_COPY.bulk.message(ids.length),
      confirmLabel: REVIEW_COPY.bulk.confirm,
      tone: 'destructive',
    })

    if (!confirmed) {
      return null
    }

    this.#setMutation(true)
    const failedIds = await deleteEntries(this.#options.gateway, ids)
    const deleted = ids.length - failedIds.length
    this.#snapshot = { ...this.#snapshot, failedDeleteIds: failedIds }
    reportBulkDeleteOutcome(this.#options.notifications, this.#options.logger, ids.length, deleted, failedIds)
    await this.#reload()
    this.#setMutation(false)
    void this.refreshPendingCount()

    return { deleted, failedIds, previousOffset: previousOffsetAfterDelete(this.#snapshot.queue) }
  }

  async saveDecision(id: FeedbackEntryId, draft: ReviewDecisionDraft): Promise<boolean> {
    if (this.#isBusy()) {
      return false
    }

    this.#setMutation(true)
    const result = await this.#options.gateway.saveDecision(id, draft)

    if (isErr(result)) {
      if (result.error.status !== 409 && result.error.status !== 422) {
        this.#options.notifications.error(REVIEW_COPY.form.failure)
      }
      this.#snapshot = { ...this.#snapshot, mutationPending: false, saveProblem: result.error }
      this.#emit()
      return false
    }

    this.#options.notifications.success(REVIEW_COPY.form.success)
    if (this.#isActive(id)) this.close()
    await this.#reload()
    this.#setMutation(false)
    void this.refreshPendingCount()
    return true
  }

  // FR-REV-015c: a trigger success re-fetches the entry (and, incidentally, the batch phase
  // alongside it) rather than patching the synthesis state in place from the outcome payload.
  async synthesize(id: FeedbackEntryId): Promise<boolean> {
    if (this.#isBusy()) {
      return false
    }

    const detailSequence = this.#detailSequence
    this.#setSynthesisPending(true)
    const result = await this.#options.gateway.synthesize(id)

    if (isErr(result)) {
      this.#snapshot = {
        ...this.#snapshot,
        synthesisPending: false,
        synthesisProblem: { kind: 'synthesize', problem: result.error },
      }
      this.#emit()
      return false
    }

    this.#options.notifications.success(REVIEW_COPY.synthesis.synthesizeSuccess(result.value.kbDocumentId))

    // The drawer may have been closed or pointed at another entry while the trigger was in
    // flight; revalidating then would abort that newer detail request with a stale entry.
    if (detailSequence === this.#detailSequence) {
      await this.open(id)
    }

    this.#setSynthesisPending(false)
    void this.refreshPendingCount()
    return true
  }

  // FR-REV-015a: a reason is asked for first (optional); an empty answer is confirmed a second
  // time before the rollback is sent, so a blank reason is a deliberate choice rather than a slip.
  async rollback(articleId: KbDocumentId): Promise<boolean> {
    if (this.#isBusy()) {
      return false
    }

    const originatingId = this.#snapshot.activeEntry?.id ?? null
    const reason = await collectRollbackReason(this.#options.confirmations)

    if (reason === null) {
      return false
    }

    this.#setSynthesisPending(true)
    const result = await this.#options.gateway.rollback(articleId, reason.length === 0 ? null : reason)

    if (isErr(result)) {
      this.#snapshot = {
        ...this.#snapshot,
        synthesisPending: false,
        synthesisProblem: { kind: 'rollback', problem: result.error },
      }
      this.#emit()
      return false
    }

    this.#options.notifications.success(REVIEW_COPY.synthesis.rollbackSuccess(result.value.entriesReverted))
    if (this.#isActive(originatingId)) this.close()
    await this.#reload()
    this.#setSynthesisPending(false)
    void this.refreshPendingCount()
    return true
  }

  async loadStats(signal: AbortSignal): Promise<void> {
    const sequence = ++this.#statsSequence
    this.#snapshot = { ...this.#snapshot, statsStatus: 'loading', statsProblem: null }
    this.#emit()

    const result = await this.#options.gateway.stats(signal)

    if (signal.aborted || sequence !== this.#statsSequence) {
      return
    }

    this.#snapshot = isErr(result)
      ? { ...this.#snapshot, statsStatus: 'error', statsProblem: result.error }
      : { ...this.#snapshot, stats: result.value, statsStatus: 'ready', statsProblem: null }
    this.#emit()
  }

  // FR-REV-020: a nav badge probe. Failures are silent — a stale or missing count is not worth
  // interrupting anyone over, so nothing is stored to report it.
  async refreshPendingCount(): Promise<void> {
    this.#pendingCountController?.abort()
    const controller = new AbortController()
    const sequence = ++this.#pendingCountSequence
    this.#pendingCountController = controller
    const result = await this.#options.gateway.pendingCount(controller.signal)

    if (sequence !== this.#pendingCountSequence || controller.signal.aborted || isErr(result)) {
      return
    }

    this.#snapshot = { ...this.#snapshot, pendingCount: result.value }
    this.#emit()
  }

  dispose(): void {
    this.#listController?.abort()
    this.#detailController?.abort()
    this.#pendingCountController?.abort()
    this.#listeners.clear()
  }

  // FR-REV-014/015: one lock across save, delete, synthesize, and rollback so a decision change
  // and a synthesis mutation can never run against the same entry at the same time.
  #isBusy(): boolean {
    return this.#snapshot.mutationPending || this.#snapshot.synthesisPending
  }

  #isActive(id: FeedbackEntryId | null): boolean {
    return (this.#snapshot.activeEntry?.id ?? null) === id
  }

  #setMutation(pending: boolean): void {
    this.#snapshot = { ...this.#snapshot, mutationPending: pending }
    this.#emit()
  }

  #setSynthesisPending(pending: boolean): void {
    this.#snapshot = { ...this.#snapshot, synthesisPending: pending, synthesisProblem: pending ? null : this.#snapshot.synthesisProblem }
    this.#emit()
  }

  async #reload(): Promise<void> {
    if (this.#currentQuery !== null) {
      await this.load(this.#currentQuery)
    }
  }

  #emit(): void {
    for (const listener of this.#listeners) {
      listener()
    }
  }
}