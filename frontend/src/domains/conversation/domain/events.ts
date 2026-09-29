import type { ConversationId } from '@/shared/kernel/branded'
import type { Instant } from '@/shared/kernel/instant'

// The `conv` context's own view of a loaded message. Deliberately not `messaging`'s `ChatMessage`:
// SPEC-011 §1 — this context publishes to messaging and never imports it. Only what the
// pending-turn rule of FR-CONV-009 needs to read is modelled here.
export type LoadedMessage = {
  readonly role: string
  readonly toolCallCount: number
}

export type ConversationSummary = {
  readonly id: ConversationId
  readonly title: string | null
  readonly updatedAt: Instant
  readonly messageCount: number
}

// FR-CONV-019. The codes CONTRACT-001 documents, plus the open string every unknown code falls
// back to, so a new server code degrades to its own `message` instead of being swallowed.
export type ConversationErrorCode =
  | 'conversation_history_unavailable'
  | 'conversation_not_found'
  | 'conversation_persistence_disabled'
  | 'invalid_conversation_request'
  | (string & {})

// The mapped union of all nine `conv`-owned server frames (CONTRACT-001 §2.4, FRAME_OWNER). Adding
// a tenth frame to the context forces a tenth member here, which `reconcileRoster` cannot compile
// without handling.
export type ConversationEvent =
  | { readonly kind: 'created'; readonly id: ConversationId }
  | { readonly kind: 'titled'; readonly id: ConversationId; readonly title: string }
  | { readonly kind: 'listed'; readonly items: readonly ConversationSummary[] }
  | {
      readonly kind: 'loaded'
      readonly id: ConversationId
      readonly messages: readonly LoadedMessage[]
    }
  | { readonly kind: 'renamed'; readonly id: ConversationId; readonly title: string }
  | { readonly kind: 'deleted'; readonly id: ConversationId }
  | {
      readonly kind: 'bulk-deleted'
      readonly deletedIds: readonly ConversationId[]
      readonly activeDeleted: boolean
    }
  | { readonly kind: 'all-deleted'; readonly count: number }
  | {
      readonly kind: 'error'
      readonly code: ConversationErrorCode
      readonly message: string
      readonly id: ConversationId | null
    }

export type ConversationEventKind = ConversationEvent['kind']
