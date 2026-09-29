import type { ConversationId } from '@/shared/kernel/branded'
import type { ConnectionState, SendResult, Unsubscribe } from '@/shared/ws/socket-client'

import type { ConversationEvent } from '@/domains/conversation/domain/events'

export type RosterPage = {
  readonly limit?: number
  readonly offset?: number
}

// SPEC-011 §3 / ADR-012: conversations are driven over the socket, so every method here is a frame
// write. `SendResult` rather than `void`: FR-CONV-017 has to tell the user a mutation did not
// happen, and a closed socket is the only way it can fail.
export interface ConversationGateway {
  requestRoster(page?: RosterPage): SendResult
  create(): SendResult
  load(id: ConversationId): SendResult
  rename(id: ConversationId, title: string): SendResult
  remove(id: ConversationId): SendResult
  removeMany(ids: readonly ConversationId[]): SendResult
  removeAll(): SendResult
  onEvent(callback: (event: ConversationEvent) => void): Unsubscribe
}

export type Cancel = () => void

// FR-CONV-009: injected, never a bare `setInterval`, so the 6 s / 20-attempt policy can be driven
// to exhaustion in a test without waiting two minutes.
export interface PendingTurnScheduler {
  every(intervalMs: number, tick: () => void): Cancel
}

// P2 / P4: authentication and conversation state are per socket, so the store has to see the
// connection come back. Declared here rather than imported from `messaging`, which this context
// never depends on (SPEC-011 §1); one `SocketClient` satisfies both.
export interface ConnectionSource {
  readonly state: ConnectionState
  onStateChange(callback: (state: ConnectionState) => void): Unsubscribe
}
