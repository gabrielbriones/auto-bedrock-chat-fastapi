import type { ConnectionState, SendResult, Unsubscribe } from '@/shared/ws/socket-client'
import type { Instant } from '@/shared/kernel/instant'
import type { MessageRole } from '@/domains/messaging/domain/message'
import type { KbCitation, ToolCall, ToolResult, TurnActivity } from '@/domains/messaging/domain/turn'

export type LoadedHistoryMessage = {
  readonly id: string | null
  readonly role: MessageRole
  readonly text: string
  readonly at: Instant
  /** Tool rounds that produced this answer, already folded by the gateway; null for user messages. */
  readonly activity: TurnActivity | null
}

// SPEC-012 §3. The gateway hands the application layer protocol-free facts: no frame types, no
// snake_case, no `timestamp` strings. Phase 4 extends this union with tool activity and metadata.
export type MessagingEvent =
  | { readonly kind: 'session-established'; readonly sessionId: string }
  | {
      readonly kind: 'answered'
      readonly text: string
      readonly messageId: string | null
      readonly conversationId: string | null
      readonly toolCalls: readonly ToolCall[]
      readonly toolResults: readonly ToolResult[]
      readonly citations: readonly KbCitation[]
      readonly truncated: boolean
      readonly configuredModel: { readonly id: string; readonly name: string }
    }
  | { readonly kind: 'failed'; readonly text: string }
  /** ADR-008 / P1: typing moves the unresolved turn into streaming; text application is T-071. */
  | { readonly kind: 'typing'; readonly text: string }
  | {
      readonly kind: 'history-loaded'
      readonly conversationId: string
      readonly messages: readonly LoadedHistoryMessage[]
    }

export interface MessagingGateway {
  sendChat(text: string): SendResult
  onEvent(callback: (event: MessagingEvent) => void): Unsubscribe
}

// The slice of `ChatSocketPort` the store consumes. The socket itself is owned by the composition
// root (ADR-004), so the store never connects or closes it.
export interface ConnectionSource {
  readonly state: ConnectionState
  onStateChange(callback: (state: ConnectionState) => void): Unsubscribe
}
