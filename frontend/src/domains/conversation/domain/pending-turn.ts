import type { ConversationId } from '@/shared/kernel/branded'

import type { LoadedMessage } from '@/domains/conversation/domain/events'

export const PENDING_TURN_INTERVAL_MS = 6_000
export const PENDING_TURN_MAX_ATTEMPTS = 20

// P8 / FR-CONV-009. The server is still working when the transcript ends on a tool result, or on an
// assistant message that asked for tools and has not come back with the answer.
export const isPendingTurn = (messages: readonly LoadedMessage[]): boolean => {
  const last = messages.at(-1)

  if (last === undefined) {
    return false
  }

  return last.role === 'tool' || (last.role === 'assistant' && last.toolCallCount > 0)
}

// DESIGN-001 §4.2. `attempts` is the whole point of this being a value object: the legacy client
// reset its counter on every reply, which made the 20-attempt ceiling unreachable (Q18).
export type PendingTurnWatch = {
  readonly conversationId: ConversationId
  readonly attempts: number
  readonly exhausted: boolean
}

export const startWatch = (conversationId: ConversationId): PendingTurnWatch => ({
  conversationId,
  attempts: 0,
  exhausted: false,
})

// FR-CONV-009a: the counter resets only when the watched conversation changes. An intermediate
// reply about the same conversation continues the existing watch, it does not restart it.
export const watchFor = (
  watch: PendingTurnWatch | null,
  conversationId: ConversationId,
): PendingTurnWatch =>
  watch !== null && watch.conversationId === conversationId ? watch : startWatch(conversationId)

// FR-CONV-009c: exhaustion is a state, not a silent stop — the caller offers a retry from it.
export const recordAttempt = (watch: PendingTurnWatch): PendingTurnWatch => {
  const attempts = watch.attempts + 1

  return { ...watch, attempts, exhausted: attempts >= PENDING_TURN_MAX_ATTEMPTS }
}

export const isPolling = (watch: PendingTurnWatch | null): boolean =>
  watch !== null && !watch.exhausted
