import type { ConfirmationPort } from '@/shared/ports/confirmation-port'
import type { ConversationId } from '@/shared/kernel/branded'
import type { Clock } from '@/shared/kernel/instant'
import type { Logger } from '@/shared/logging/logger'
import type { NotificationPort } from '@/shared/ports/notification-port'

import type { Conversation } from '@/domains/conversation/domain/conversation'
import type { PendingTurnWatch } from '@/domains/conversation/domain/pending-turn'
import type { SelectionState } from '@/domains/conversation/domain/roster'
import type {
  ConnectionSource,
  ConversationGateway,
  PendingTurnScheduler,
} from '@/domains/conversation/application/ports'

// The read model the sidebar and the chat route render. Flattened out of `ConversationRoster` on
// purpose: components consume facts, never the aggregate they would be tempted to mutate.
export type ConversationSnapshot = {
  readonly visible: boolean
  readonly items: readonly Conversation[]
  readonly activeId: ConversationId | null
  readonly selection: ReadonlySet<ConversationId>
  readonly selectionState: SelectionState
  readonly bulkDeleteInFlight: boolean
  readonly connected: boolean
  /** FR-CONV-019 `conversation_history_unavailable`: shown in place, not as a toast. */
  readonly notice: string | null
  /** FR-CONV-009: non-null while the server is still working on a previously sent turn. */
  readonly pendingTurn: PendingTurnWatch | null
  /** FR-MSG-008 / FR-CONV-010: a URL named a conversation the server does not have. */
  readonly unknownId: ConversationId | null
  /** FR-CONV-002 / FR-CONV-011: a new, unsaved conversation, which lives at `/chat/ui`. */
  readonly awaitingId: boolean
}

export type ConversationStoreOptions = {
  readonly gateway: ConversationGateway
  readonly connection: ConnectionSource
  readonly confirmations: ConfirmationPort
  readonly notifications: NotificationPort
  readonly scheduler: PendingTurnScheduler
  readonly clock: Clock
  readonly logger: Logger
  /** Bootstrap's `conversationPersistenceEnabled` (FR-CONV-001). */
  readonly persistenceEnabled: boolean
}
