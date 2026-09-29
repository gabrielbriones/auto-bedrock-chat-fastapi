import type { ConversationId } from '@/shared/kernel/branded'
import type { Instant } from '@/shared/kernel/instant'
import { err, ok, type Result } from '@/shared/kernel/result'

// DESIGN-001 §4.1. A durable thread as the roster knows it: the summary the server lists, not the
// transcript. Messages belong to `messaging`, which this context never imports (SPEC-011 §1).
export type Conversation = {
  readonly id: ConversationId
  /** Null until the server titles the thread; presentation supplies the fallback (FR-CONV-014). */
  readonly title: string | null
  readonly updatedAt: Instant
  readonly messageCount: number
}

export type EmptyTitle = { readonly kind: 'empty-title' }

// I3. A negative count can only come from a malformed payload, and refusing the whole conversation
// over it would drop a thread the user can still open — so it is clamped, not rejected.
const atLeastZero = (messageCount: number): number =>
  Number.isFinite(messageCount) && messageCount > 0 ? Math.floor(messageCount) : 0

export const createConversation = (
  id: ConversationId,
  updatedAt: Instant,
  title: string | null = null,
  messageCount = 0,
): Conversation => ({ id, title, updatedAt, messageCount: atLeastZero(messageCount) })

// I2: a rename must carry a non-empty trimmed title. Returned as a `Result` because this is the
// one conversation rule a user can break, and `FR-CONV-004` renders it as a field error.
export const renameConversation = (
  conversation: Conversation,
  title: string,
  at: Instant,
): Result<Conversation, EmptyTitle> => {
  const trimmed = title.trim()

  return trimmed === ''
    ? err({ kind: 'empty-title' })
    : ok({ ...conversation, title: trimmed, updatedAt: at })
}

// I1: `id` is immutable, so a merge never takes the incoming id. Everything else is last-write-wins
// except a title the frame omits, which must not blank one the roster already has (FR-CONV-008).
export const mergeConversation = (
  conversation: Conversation,
  patch: Partial<Omit<Conversation, 'id'>>,
): Conversation => ({
  id: conversation.id,
  title: patch.title ?? conversation.title,
  updatedAt: patch.updatedAt ?? conversation.updatedAt,
  messageCount: atLeastZero(patch.messageCount ?? conversation.messageCount),
})

// FR-CONV-013: newest first, with the id as a tie-break so an unstable sort cannot reorder two
// conversations that share a timestamp between renders.
export const byRecencyThenId = (left: Conversation, right: Conversation): number =>
  right.updatedAt.compare(left.updatedAt) || (left.id < right.id ? -1 : left.id > right.id ? 1 : 0)
