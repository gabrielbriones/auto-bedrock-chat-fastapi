import type { ConversationId } from '@/shared/kernel/branded'
import type { Instant } from '@/shared/kernel/instant'

import { createConversation, type Conversation } from '@/domains/conversation/domain/conversation'
import type { ConversationEvent, ConversationSummary } from '@/domains/conversation/domain/events'
import {
  activate,
  clearActive,
  clearItems,
  clearSelection,
  completeBulkDelete,
  recoverActiveAfterRefresh,
  releaseBulkDelete,
  removeMany,
  replaceItems,
  upsert,
  type ConversationRoster,
} from '@/domains/conversation/domain/roster'

const toConversation = (summary: ConversationSummary) =>
  createConversation(summary.id, summary.updatedAt, summary.title, summary.messageCount)

// FR-CONV-003 / FR-CONV-011: upsert before activating, so the thread is listed by the time
// `activate` checks I4. `patch` carries the frame's own dating rule (FR-CONV-008).
const adopt = (
  roster: ConversationRoster,
  id: ConversationId,
  patch: Partial<Omit<Conversation, 'id'>>,
  at: Instant,
) => activate(upsert(roster, id, patch, at), id)

/**
 * SPEC-011 §4. One reducer replaces the nine ad-hoc patches the legacy client applied from nine
 * frame handlers, so "what is the roster right now" is decided in exactly one place.
 *
 * Pure: no socket, no clock, no `Date.now()` — `at` is read from the injected `Clock` by the
 * caller, so the same sequence always reconciles to the same roster.
 *
 * The declared return type is what makes a tenth `ConversationEvent` kind a compile error: an
 * unhandled member leaves that branch returning `undefined`.
 */
export const reconcileRoster = (
  roster: ConversationRoster,
  event: ConversationEvent,
  at: Instant,
): ConversationRoster => {
  switch (event.kind) {
    // FR-CONV-008: the server has named the thread the pending first turn belongs to.
    case 'created':
      return adopt(roster, event.id, { updatedAt: at }, at)

    // FR-CONV-008. `titled` arriving before `created` is not an error: the upsert inserts the
    // thread with the title it carries, and the later `created` merges onto it.
    case 'titled':
    case 'renamed':
      return upsert(roster, event.id, { title: event.title, updatedAt: at }, at)

    // FR-CONV-016: a single page, so the list replaces rather than merges. FR-CONV-010 then adopts
    // the newest thread if a turn was sent before its id existed.
    case 'listed':
      return recoverActiveAfterRefresh(replaceItems(roster, event.items.map(toConversation)))

    // Opening a thread is not activity on it, so `updatedAt` is left alone and the list holds still.
    case 'loaded':
      return adopt(roster, event.id, {}, at)

    // Normalisation clears `activeId` when the deleted thread was the active one (I4).
    case 'deleted':
      return removeMany(roster, [event.id])

    // FR-CONV-006a / FR-CONV-006b.
    case 'bulk-deleted': {
      const next = completeBulkDelete(roster, event.deletedIds)
      return event.activeDeleted ? clearActive(next) : next
    }

    // FR-CONV-018.
    case 'all-deleted':
      return clearSelection(clearItems(releaseBulkDelete(roster)))

    // FR-CONV-019: whatever else the code means, the guard must come off or every later bulk delete
    // is blocked by a request that already failed. Code-to-copy mapping is the caller's job.
    case 'error':
      return releaseBulkDelete(roster)
  }
}
