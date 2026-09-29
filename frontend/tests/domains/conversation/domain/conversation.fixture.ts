import { conversationId, type ConversationId } from '@/shared/kernel/branded'
import { Instant } from '@/shared/kernel/instant'

import { createConversation, type Conversation } from '@/domains/conversation/domain/conversation'
import type { ConversationEvent } from '@/domains/conversation/domain/events'
import { emptyRoster, replaceItems, type ConversationRoster } from '@/domains/conversation/domain/roster'

const BASE_EPOCH_MS = Date.UTC(2026, 7, 25, 12)

export const at = (offsetMinutes: number): Instant => {
  const instant = Instant.fromEpochMilliseconds(BASE_EPOCH_MS + offsetMinutes * 60_000)

  if (instant.kind === 'err') {
    throw new Error('unbuildable fixture instant')
  }

  return instant.value
}

export const id = (name: string): ConversationId => conversationId(name)

export const aConversation = (
  name: string,
  offsetMinutes = 0,
  title: string | null = null,
  messageCount = 0,
): Conversation => createConversation(id(name), at(offsetMinutes), title, messageCount)

export const aRoster = (items: readonly Conversation[]): ConversationRoster =>
  replaceItems(emptyRoster, items)

// The nine `conv`-owned frames as domain events (CONTRACT-001 §2.3), so a test names a case rather
// than rebuilding its shape. The recorded wire fixtures live in `tests/msw/fixtures/ws`.
export const anEvent = {
  created: (name: string): ConversationEvent => ({ kind: 'created', id: id(name) }),
  titled: (name: string, title: string): ConversationEvent => ({
    kind: 'titled',
    id: id(name),
    title,
  }),
  listed: (items: readonly Conversation[]): ConversationEvent => ({ kind: 'listed', items }),
  loaded: (name: string, roles: readonly string[] = []): ConversationEvent => ({
    kind: 'loaded',
    id: id(name),
    messages: roles.map((role) => ({ role, toolCallCount: 0 })),
  }),
  renamed: (name: string, title: string): ConversationEvent => ({
    kind: 'renamed',
    id: id(name),
    title,
  }),
  deleted: (name: string): ConversationEvent => ({ kind: 'deleted', id: id(name) }),
  bulkDeleted: (names: readonly string[], activeDeleted = false): ConversationEvent => ({
    kind: 'bulk-deleted',
    deletedIds: names.map(id),
    activeDeleted,
  }),
  allDeleted: (count: number): ConversationEvent => ({ kind: 'all-deleted', count }),
  error: (code: string, message = 'nope', name: string | null = null): ConversationEvent => ({
    kind: 'error',
    code,
    message,
    id: name === null ? null : id(name),
  }),
} as const
