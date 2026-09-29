import type { ConversationId } from '@/shared/kernel/branded'

import {
  PENDING_TURN_INTERVAL_MS,
  isPolling,
  recordAttempt,
  startWatch,
  watchFor,
  type PendingTurnWatch,
} from '@/domains/conversation/domain/pending-turn'
import type { Cancel, PendingTurnScheduler } from '@/domains/conversation/application/ports'

export type PendingTurnWatcherOptions = {
  readonly scheduler: PendingTurnScheduler
  /** Re-issues `conversation_load` for the watched thread (FR-CONV-009). */
  readonly poll: (id: ConversationId) => void
  readonly onChange: () => void
}

// SPEC-011 §4 `watchPendingTurn`. Split out of the store because the counter policy is the whole
// requirement: `FR-CONV-009a` says it resets only on a new conversation id, so nothing here may
// call `startWatch` on an intermediate reply.
export class PendingTurnWatcher {
  readonly #options: PendingTurnWatcherOptions
  #cancel: Cancel | null = null
  #watch: PendingTurnWatch | null = null

  constructor(options: PendingTurnWatcherOptions) {
    this.#options = options
  }

  get watch(): PendingTurnWatch | null {
    return this.#watch
  }

  // FR-CONV-009 / FR-CONV-009a. A reply about the watched conversation that is still pending
  // continues the existing watch; only a different id starts a new one.
  observe(id: ConversationId, pending: boolean): void {
    if (!pending) {
      this.stop()
      return
    }

    const restarted = this.#watch === null || this.#watch.conversationId !== id
    this.#watch = watchFor(this.#watch, id)

    // An exhausted watch stays stopped: the reply to the last poll must not start the clock again,
    // or the ceiling would never be reached (FR-CONV-009c).
    if (isPolling(this.#watch) && (restarted || this.#cancel === null)) {
      this.#schedule()
    }

    this.#options.onChange()
  }

  /** FR-CONV-009b: navigating away or losing the socket ends the watch immediately. */
  stop(): void {
    if (this.#watch === null && this.#cancel === null) {
      return
    }

    this.#clear()
    this.#watch = null
    this.#options.onChange()
  }

  // FR-CONV-009c: the retry resumes from zero, which is the only place the counter is allowed to
  // reset without the conversation changing — because the user asked for it.
  retry(): void {
    if (this.#watch === null) {
      return
    }

    this.#watch = startWatch(this.#watch.conversationId)
    this.#schedule()
    this.#options.onChange()
  }

  dispose(): void {
    this.#clear()
    this.#watch = null
  }

  #schedule(): void {
    this.#clear()
    this.#cancel = this.#options.scheduler.every(PENDING_TURN_INTERVAL_MS, () => {
      this.#tick()
    })
  }

  // Each tick counts, then polls: the twentieth attempt still asks, and only afterwards does the
  // watch stop and offer a retry.
  #tick(): void {
    if (this.#watch === null) {
      return
    }

    const watch = recordAttempt(this.#watch)
    this.#watch = watch
    this.#options.poll(watch.conversationId)

    if (!isPolling(watch)) {
      this.#clear()
    }

    this.#options.onChange()
  }

  #clear(): void {
    this.#cancel?.()
    this.#cancel = null
  }
}
