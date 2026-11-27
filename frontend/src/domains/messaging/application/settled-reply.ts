import type { ConversationId } from '@/shared/kernel/branded'

import type { Turn } from '@/domains/messaging/domain/turn'

/** FR-MSG-032: a turn this session sent has just resolved with a reply the user can read. */
export type SettledReply = {
  readonly outcome: 'answered' | 'failed'
  readonly text: string
  readonly conversationId: ConversationId | null
}

/** The reply a just-resolved turn settled with; `null` unless it was answered or failed. */
export const settledReplyOf = (turn: Turn | undefined): SettledReply | null =>
  turn === undefined || turn.response === null || (turn.status !== 'answered' && turn.status !== 'failed')
    ? null
    : { outcome: turn.status, text: turn.response.raw, conversationId: turn.conversationId }

// FR-MSG-032. Only a pending turn settling counts: transients, abandoned turns and loaded
// history are not a reply the user is waiting on.
export class ReplyAnnouncer {
  readonly #listeners = new Set<(reply: SettledReply) => void>()

  subscribe = (listener: (reply: SettledReply) => void): (() => void) => {
    this.#listeners.add(listener)

    return () => {
      this.#listeners.delete(listener)
    }
  }

  announce(turn: Turn | undefined): void {
    const reply = settledReplyOf(turn)
    if (reply !== null) {
      this.#listeners.forEach((listener) => listener(reply))
    }
  }

  clear(): void {
    this.#listeners.clear()
  }
}
