import type { MessageId } from '@/shared/kernel/branded'
import type { Instant } from '@/shared/kernel/instant'

export type MessageRole = 'user' | 'assistant' | 'system'

// `raw` is never trusted: rendering always goes through the sanitising pipeline (NFR-SEC-001).
// The full `MessageContent` VO with its `kind` discriminator arrives with markdown (T-072).
export type ChatMessage = {
  readonly id: MessageId | null
  readonly role: MessageRole
  readonly raw: string
  readonly at: Instant
  /** Tie-breaks messages that share a timestamp; monotonic within one session. */
  readonly seq: number
}

export const createMessage = (
  role: MessageRole,
  raw: string,
  at: Instant,
  seq: number,
  id: MessageId | null = null,
): ChatMessage => ({ id, role, raw, at, seq })
